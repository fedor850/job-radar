"""The radar pipeline: poll sources → dedupe → prefilter → AI triage → details → AI score → notify."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import anthropic

from .ai import BLOCKER_LABEL, triage_verdict
from .links import best_link, classify_link, guess_ats_link, link_rank, match_in_db
from .models import RawJob
from .sources.boards import detect_board
from .sources.linkedin import LinkedInBlocked
from .sources.linkedin import LinkedInSource
from .sources.original import SNIPPET_SOURCES, fetch_posting, find_original, has_student_signal
from .util import company_key, norm_title, fingerprint, iso, local, now_utc, parse_iso

if TYPE_CHECKING:
    from .bot import Notifier

log = logging.getLogger("jobradar.pipeline")

LEARNABLE = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday", "personio", "recruitee", "workable",
             "teamtailor", "bamboohr", "breezy", "pinpoint", "join"}


CUT_SOURCES = ("adzuna",)  # aggregators whose page shows only the start of the ad


class AIUnavailable(RuntimeError):
    pass


# triage rejects only on evidence found in the title (ai.triage_verdict); everything else goes to full scoring,
# where every blocker needs a verbatim quote (ai.verify_blockers)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zäöüß]{4,}", (text or "")[:2500].lower()))


class Pipeline:
    def __init__(self, settings, db, ai, sheets, notifier: "Notifier", sources: dict):
        self.settings = settings
        self.db = db
        self.ai = ai
        self.sheets = sheets
        self.notifier = notifier
        self.sources = sources
        self.sem = asyncio.Semaphore(int(settings.get("ai.concurrency", 4)))
        self.locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in sources}
        #: LinkedIn job pages are opened ONE at a time (a burst of parallel requests gets 429; the waiting
        #: coroutines cost nothing, whereas waiting worker threads would starve the other sources)
        self.detail_sem: dict[str, asyncio.Semaphore] = {"linkedin": asyncio.Semaphore(1)}
        self.blocking = set(settings.get("filters.blocking", ["fulltime_only", "german_required", "german_ad", "senior",
                                                                "wrong_field", "not_vienna", "eu_only",
                                                                "not_eligible", "expired"]))
        self.title_exclude = [re.compile(p, re.I) for p in settings.get("filters.title_exclude", [])]
        self.site_names = {s.get("name") for s in (settings.get("sources.sites", []) or []) if isinstance(s, dict)}
        self.company_exclude = [c.lower() for c in settings.get("filters.company_exclude", [])]

    # ------------------------------------------------------------------ settings at runtime
    @property
    def min_score(self) -> int:
        return int(self.db.kv_get("min_score", self.settings.get("filters.min_score", 40)))

    @property
    def paused(self) -> bool:
        return bool(self.db.kv_get("paused", False))

    # ------------------------------------------------------------------ polling
    async def run_source(self, name: str, backfill: bool = False, silent: bool = False) -> dict:
        source = self.sources[name]
        lock = self.locks[name]
        if lock.locked():
            return {"skipped": True}
        async with lock:
            try:
                raws = await asyncio.to_thread(source.fetch, backfill)
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                level = logging.WARNING if isinstance(e, LinkedInBlocked) else logging.ERROR
                log.log(level, "source %s failed: %s", name, msg)
                h = self.db.health_run(name, False, error=msg)
                await self.notifier.source_health(name, h)
                return {"error": msg}
            listed = getattr(source, "last_list_count", None)  # site sources: links on the list pages
            self._track_zero_yield(name, len(raws) if listed is None else listed, backfill)
            first_run = not backfill and not self.db.one(
                "SELECT 1 FROM source_health WHERE name=? AND last_ok IS NOT NULL", (name,))
            queued = self.ingest(raws, name, backfill, first_run=first_run)
            h = self.db.health_run(name, True, items=len(raws), new=len(queued))
            await self.notifier.source_health(name, h)
            if queued:
                log.info("%s: %d fetched, %d new to evaluate", name, len(raws), len(queued))
            results = await asyncio.gather(
                *(self.evaluate(job_id, raw, name, silent=silent or bool(raw.meta.get("initial")), backfill=backfill)
                  for job_id, raw in queued),
                return_exceptions=True,
            )
            sent = sum(1 for r in results if r == "sent")
            return {"fetched": len(raws), "new": len(queued), "sent": sent}

    async def run_raws(self, name: str, raws: list[RawJob]) -> dict:
        """Evaluate jobs that did not come from polling `name` itself (LinkedIn Job Alert e-mails).

        ingest() drops every uid that is already known (the scraper found it first) and every duplicate by
        company+title / description, so a job can never be sent twice."""
        if name not in self.sources or not raws:
            return {"new": 0, "sent": 0}
        queued = self.ingest(raws, name, backfill=False, first_run=False)
        results = await asyncio.gather(*(self.evaluate(jid, raw, name) for jid, raw in queued),
                                       return_exceptions=True)
        return {"new": len(queued), "sent": sum(1 for r in results if r == "sent")}

    def _track_zero_yield(self, name: str, n: int, backfill: bool) -> None:
        """A source that keeps answering 'OK, 0 ads' is broken silently (site changed its HTML, API key used
        up, …) — the failure alert never fires. Count such runs; the bot's health job alerts after 8 h."""
        if backfill or (name == "linkedin" and time.time() < LinkedInSource.cooldown_until):
            return
        key = f"zero:{name}"
        z = self.db.kv_get(key)
        if n:
            if z:
                self.db.kv_set(key, None)
        else:
            z = z or {"n": 0, "since": iso(now_utc())}
            z["n"] = int(z.get("n", 0)) + 1
            self.db.kv_set(key, z)

    # ------------------------------------------------------------------ ingest (sync, in event loop)
    def ingest(self, raws: list[RawJob], source_name: str, backfill: bool,
               first_run: bool = False) -> list[tuple[int, RawJob]]:
        """first_run: the source has never been polled successfully → its backlog is not "new": only ads from the
        last `general.new_source_days` days are evaluated (and delivered silently), older ones are just remembered.
        The same applies to a company site seen for the first time."""
        queued: list[tuple[int, RawJob]] = []
        boot_hours = int(self.settings.get("general.bootstrap_hours", 48))
        new_days = int(self.settings.get("general.new_source_days", 14))
        backfill_days = int(self.settings.get("general.backfill_days", 10))
        max_age = int(self.settings.get("general.max_age_days", 10))
        for raw in raws:
            if not raw.title:
                continue
            raw.title = html.unescape(raw.title).strip()
            raw.company = html.unescape(raw.company or "").strip()
            if self.db.job_exists(raw.uid):
                continue
            if raw.meta.get("drop"):
                if raw.meta.get("remember_drop", raw.source in self.site_names):
                    self.db.insert_job(raw, fingerprint(raw.company, raw.title), stage="prefiltered_out",
                                       reason="dropped by source (location/removed)")
                continue
            self.learn_boards(raw)
            fp = fingerprint(raw.company, raw.title)
            if raw.posted_at and (now_utc() - raw.posted_at) > timedelta(days=max_age):
                self.db.insert_job(raw, fp, stage="bootstrap", reason=f"older than {max_age} days")
                continue
            age = (now_utc() - raw.posted_at) if raw.posted_at else None
            if (raw.meta.get("board_new") or first_run) and not backfill:
                if age is not None and age <= timedelta(hours=boot_hours):
                    pass  # genuinely fresh — treat as a normal new ad
                elif (age is not None and age <= timedelta(days=new_days)) or (age is None and (
                        first_run or raw.meta.get("board_new"))):
                    # recent backlog of a new source (or a source without dates, i.e. currently open ads):
                    # evaluate and deliver without sound
                    raw.meta["initial"] = True
                else:
                    self.db.insert_job(raw, fp, stage="bootstrap")
                    continue
            if backfill and raw.posted_at and (now_utc() - raw.posted_at) > timedelta(days=backfill_days):
                self.db.insert_job(raw, fp, stage="bootstrap", reason="older than backfill window")
                continue
            dup = self.db.find_by_fingerprint(fp, days=45, exclude_uid=raw.uid) or self._similar_dup(raw)
            if dup:
                self.db.insert_job(raw, fp, stage="duplicate", dup_of=dup["id"])
                self.maybe_upgrade_link(dup, raw)
                continue
            ok, reason = self.prefilter(raw)
            if not ok:
                self.db.insert_job(raw, fp, stage="prefiltered_out", reason=reason)
                continue
            if raw.needs_detail:
                raw.meta["pending_detail"] = True  # survives a failed attempt: a retry must fetch the full ad again
            job_id = self.db.insert_job(raw, fp, stage="queued")
            if job_id:
                queued.append((job_id, raw))
        return queued

    def _similar_dup(self, raw: RawJob) -> dict | None:
        """Same title from another source where the company is missing/different spelling: compare descriptions."""
        tk = norm_title(raw.title)
        if len(tk.split()) < 3:
            return None
        my_key = company_key(raw.company) if raw.company else ""
        mine = _words(raw.description)
        for c in self.db.find_same_title(tk, days=45, exclude_uid=raw.uid):
            their_key = company_key(c.get("company") or "")
            if my_key and their_key and my_key != their_key:
                continue
            theirs = _words(c.get("description") or "")
            if mine and theirs:
                if len(mine & theirs) / max(1, len(mine | theirs)) >= 0.4:
                    return c
            elif not my_key or not their_key:
                # no text to compare and a company is missing: same specific title is good enough
                return c
        return None

    def _sent_duplicate(self, job: dict) -> dict | None:
        """A job already delivered in the last 45 days with (almost) the same description.

        Catches re-posts that the fingerprint misses: another company spelling ("RM Consulting" vs "Russmedia
        Equity Partners") and a longer title. Thresholds calibrated on the sent history: true duplicates ≥ 0.59,
        different ads of the same employer ≤ 0.4 (shared boilerplate); same employer needs ≥ 0.95."""
        mine = _words(job.get("description"))
        if len(mine) < 40:
            return None
        my_key = company_key(job.get("company") or "")
        my_title = set(norm_title(job.get("title") or "").split())
        since = iso(now_utc() - timedelta(days=45))
        rows = self.db.query("SELECT id, title, company, description FROM jobs WHERE sent_at IS NOT NULL AND "
                             "sent_at>=? AND id<>?", (since, job["id"]))
        for r in rows:
            theirs = _words(r["description"])
            if len(theirs) < 40:
                continue
            sim = len(mine & theirs) / max(1, len(mine | theirs))
            same_company = my_key and my_key == company_key(r["company"] or "")
            if same_company and sim >= 0.95:
                return dict(r)
            shared_title = my_title & set(norm_title(r["title"] or "").split())
            if not same_company and sim >= 0.6 and len({w for w in shared_title if len(w) > 3}) >= 1:
                return dict(r)
        return None

    def prefilter(self, raw: RawJob) -> tuple[bool, str]:
        for pat in self.title_exclude:
            if pat.search(raw.title):
                return False, f"title:{pat.pattern}"
        comp = (raw.company or "").lower()
        for c in self.company_exclude:
            if c and c in comp:
                return False, f"company:{c}"
        if raw.meta.get("query_remote"):
            blob = f"{raw.title} {raw.location}".lower()
            if not re.search(r"remote|home\s*office|homeoffice|anywhere", blob):
                return False, "not remote"
        return True, ""

    def learn_boards(self, raw: RawJob) -> None:
        if not self.settings.get("boards.learn", True):
            return
        for url in (raw.apply_url, raw.url):
            b = detect_board(url) if url else None
            if not b or b[0] not in LEARNABLE:
                continue
            kind, key, params = b
            if self.db.upsert_board(kind, key, raw.company, "learned", params):
                log.info("learned new board %s:%s (%s)", kind, key, raw.company)

    def maybe_upgrade_link(self, dup: dict, raw: RawJob) -> None:
        new_url = raw.apply_url or raw.url
        new_kind = classify_link(new_url)
        cur_url = dup.get("apply_url") or dup.get("url") or ""
        cur_kind = classify_link(cur_url)
        if link_rank(new_kind) > link_rank(cur_kind):
            self.db.update_job(dup["id"], apply_url=new_url, apply_kind=new_kind)
            if dup.get("tg_message_id"):
                asyncio.get_running_loop().create_task(self.notifier.refresh_job(dup["id"]))

    # ------------------------------------------------------------------ evaluation
    async def evaluate(self, job_id: int, raw: RawJob, source_name: str, silent: bool = False,
                       backfill: bool = False) -> str:
        try:
            return await self._evaluate(job_id, raw, source_name, silent, backfill)
        except AIUnavailable as e:  # "AI:" prefix → retried until the API is back (not limited by attempts)
            self.db.update_job(job_id, stage="error", reason=f"AI: {e}"[:300])
            return "error"
        except Exception as e:  # noqa: BLE001
            log.exception("evaluate %s failed", raw.uid)
            self.db.update_job(job_id, stage="error", reason=f"{type(e).__name__}: {e}"[:300])
            return "error"

    async def _evaluate(self, job_id: int, raw: RawJob, source_name: str, silent: bool, backfill: bool = False) -> str:
        source = self.sources[source_name]
        async with self.sem:
            job = self.db.get_job(job_id)
            pa = parse_iso(job.get("posted_at")) if job.get("posted_at") else None
            max_age = int(self.settings.get("general.max_age_days", 10))
            if pa and (now_utc() - pa) > timedelta(days=max_age):
                self.db.update_job(job_id, stage="bootstrap", reason=f"older than {max_age} days")
                return "bootstrap"
            # 1) cheap triage on title/snippet
            if self.settings.get("ai.triage", True):
                t = await self._ai(self.ai.triage(job))
                reject, why = triage_verdict(t, job)
                if reject:
                    self.db.update_job(job_id, stage="triage_reject", reason=why)
                    return "triage_reject"
                if t.get("obviously_irrelevant"):
                    log.info("triage reject ignored (%s): %s", why, job.get("title"))
            # 2) details (description, apply link)
            if raw.needs_detail:
                try:
                    dsem = self.detail_sem.get(source_name)
                    if dsem is not None:
                        async with dsem:
                            raw = await asyncio.to_thread(source.fetch_detail, raw)
                    else:
                        raw = await asyncio.to_thread(source.fetch_detail, raw)
                except LinkedInBlocked as e:
                    # LinkedIn is throttled: try the same ad on the company's own site / ATS / JOIN (free lookups)
                    # instead of waiting for the pause to end
                    try:
                        orig = await asyncio.to_thread(find_original, self.db, raw.company, raw.title)
                    except Exception:  # noqa: BLE001
                        orig = None
                    if not orig or len(orig.get("description") or "") < 400:
                        self.db.update_job(job_id, stage="error", reason=str(e))
                        return "error"
                    raw.description = orig["description"][:15000]
                    raw.meta["full_text_from"] = orig.get("via") or "original"
                    if orig.get("url") and classify_link(orig["url"]) == "company":
                        raw.apply_url = orig["url"]
                    raw.needs_detail = False
                    log.info("linkedin throttled — full text for '%s' via %s", raw.title, raw.meta["full_text_from"])
                raw.meta.pop("pending_detail", None)
                if raw.meta.get("drop"):
                    self.db.update_job(job_id, stage="prefiltered_out", reason="location after detail")
                    return "prefiltered_out"
                self.learn_boards(raw)
                if raw.meta.get("from_alert"):
                    # title/company came from an e-mail; now that LinkedIn's own are known, dedupe again
                    fp = fingerprint(raw.company, raw.title)
                    dup = self.db.find_by_fingerprint(fp, days=45, exclude_uid=raw.uid) or self._similar_dup(raw)
                    if dup:
                        self.db.update_job(job_id, stage="duplicate", dup_of=dup["id"], title=raw.title,
                                           company=raw.company, fingerprint=fp, reason="job alert: already known")
                        return "duplicate"
                    self.db.update_job(job_id, fingerprint=fp)
                self.db.update_job(job_id, description=raw.description, apply_url=raw.apply_url, location=raw.location,
                                   title=raw.title, company=raw.company,
                                   company_linkedin=raw.company_linkedin, meta=raw.meta, url=raw.url,
                                   posted_at=iso(raw.posted_at) if raw.posted_at else None)
                job = self.db.get_job(job_id)
            # 2b) the ad text is visibly cut off (Adzuna shows only the start; "…" at the end): look for the original
            job = await self._complete_cut_text(job_id, job)
            # 3) full scoring
            today = local(now_utc()).strftime("%Y-%m-%d (%A)")
            a = await self._ai(self.ai.score(job, today))
        score = int(a.get("score", 0))
        blockers = set(a.get("blockers") or []) & self.blocking
        if a.get("_unverified") and not blockers and score < self.min_score:
            # the model kept a reason it could not prove even after a re-ask: never hide a job on a guess —
            # deliver it at the threshold with a warning instead
            names = ", ".join(BLOCKER_LABEL.get(b, b) for b in a["_unverified"])
            risks = [r for r in (a.get("risks") or []) if not r.startswith("⚠️ The AI suspected")]
            a["risks"] = [f"⚠️ The AI suspected: {names} — but the ad does not say so; check it yourself"] + risks[:2]
            a["_score_raised_from"] = score
            score = self.min_score
            a["score"] = score
        self.db.update_job(job_id, score=score, analysis=a)
        jm = job.get("meta") or {}
        if (jm.get("snippet_only") or jm.get("text_cut")) and not blockers and score >= self.min_score:
            res = await self._snippet_only(job_id, today)
            if res == "blocked":
                return "blocked"
            if res == "rescored":
                job = self.db.get_job(job_id)
                a = job.get("analysis") or {}
                score = int(job.get("score") or 0)
                blockers = set(a.get("blockers") or []) & self.blocking
        if blockers:
            self.db.update_job(job_id, stage="blocked", reason=",".join(sorted(blockers)))
            return "blocked"
        if score < self.min_score:
            self.db.update_job(job_id, stage="below_threshold")
            return "below_threshold"
        # 4) the same ad already delivered under another company name / title (e.g. a recruiter re-post)?
        job = self.db.get_job(job_id)
        dup = self._sent_duplicate(job)
        if dup:
            self.db.update_job(job_id, stage="duplicate", dup_of=dup["id"],
                               reason=f"same text as sent job {dup['id']} ({dup.get('company')})")
            log.info("job %s is a re-post of sent job %s — not sent again", job_id, dup["id"])
            return "duplicate"
        # 5) best apply link
        url, kind = best_link(job.get("apply_url") or "", job.get("url") or "")
        if kind != "company":
            other = match_in_db(self.db, job["company"], job["title"], exclude_uid=job["uid"])
            if other:
                url, kind = other, "company"
        self.db.update_job(job_id, apply_url=url, apply_kind=kind)
        # 6) notify
        if backfill:
            self.db.update_job(job_id, stage="held_backfill")
            return "held"
        if self.paused or not self.notifier.ready:
            self.db.update_job(job_id, stage="held")
            return "held"
        await self.deliver(job_id, silent=silent)
        if kind != "company" and self.settings.get("links.resolve", True):
            asyncio.get_running_loop().create_task(self.resolve_company_link(job_id))
        return "sent"

    async def _complete_cut_text(self, job_id: int, job: dict) -> dict:
        """Free lookups for the full ad when the text we have is obviously only the beginning of it."""
        meta = job.get("meta") or {}
        fam = job["source"].split(":")[0]
        d = (job.get("description") or "").rstrip()
        if meta.get("snippet_only") or meta.get("full_text_from") or fam in SNIPPET_SOURCES:
            return job
        if not ((d.endswith(("…", "...")) and len(d) < 1500) or (fam in CUT_SOURCES and len(d) < 900)):
            return job
        try:
            orig = await asyncio.to_thread(find_original, self.db, job["company"], job["title"])
        except Exception:  # noqa: BLE001
            orig = None
        if orig and len(orig.get("description") or "") > len(d) + 200:
            meta["snippet"] = d[:600]
            meta["full_text_from"] = orig.get("via") or "original"
            upd = {"description": orig["description"][:15000], "meta": meta}
            if orig.get("url") and classify_link(orig["url"]) == "company":
                upd["apply_url"] = orig["url"]
            self.db.update_job(job_id, **upd)
            log.info("cut-off text of '%s' replaced by the original (%s)", job["title"], meta["full_text_from"])
        else:
            meta["text_cut"] = True
            self.db.update_job(job_id, meta=meta)
        return self.db.get_job(job_id)

    async def _snippet_only(self, job_id: int, today: str) -> str:
        """Jooble/Careerjet job where only the API snippet is known and the free lookups found no original.

        A job that would be sent gets one paid web search for its original posting (daily cap). If the original
        is found it is re-scored on the full text; otherwise the job is sent only when the title/snippet clearly
        says it is student-compatible (Praktikum, Werkstudent, Teilzeit, 20 h, ...). Returns 'rescored',
        'blocked' or 'keep'."""
        job = self.db.get_job(job_id)
        meta = job.get("meta") or {}
        cap = int(self.settings.get("snippet.search_daily_cap", 8))
        day_key = f"snippet_search:{now_utc().strftime('%Y-%m-%d')}"
        used = int(self.db.kv_get(day_key, 0) or 0)
        if cap and used < cap and self.settings.get("snippet.web_search", True):
            self.db.kv_set(day_key, used + 1)
            try:
                url = await self.ai.find_company_posting(job["company"], job["title"], job.get("location") or "Vienna",
                                                         kind="snippet_search")
            except Exception as e:  # noqa: BLE001
                log.info("snippet search failed: %s", e)
                url = None
            orig = await asyncio.to_thread(fetch_posting, url, job["title"]) if url else None
            if orig and len(orig["description"]) > len(job.get("description") or "") + 200:
                meta.pop("snippet_only", None)
                meta.pop("text_cut", None)
                meta["snippet"] = (job.get("description") or "")[:600]
                meta["full_text_from"] = "web_search"
                for k in ("employment_type", "salary"):
                    if orig.get(k) and not meta.get(k):
                        meta[k] = orig[k]
                upd = {"description": orig["description"], "meta": meta}
                if classify_link(orig["url"]) == "company":
                    upd["apply_url"] = orig["url"]
                self.db.update_job(job_id, **upd)
                job = self.db.get_job(job_id)
                a = await self._ai(self.ai.score(job, today))
                self.db.update_job(job_id, score=int(a.get("score", 0)), analysis=a)
                log.info("snippet job %s re-scored on full text: %s", job_id, a.get("score"))
                return "rescored"
        a = job.get("analysis") or {}
        if not meta.get("snippet_only"):  # only the start of the ad is known, but the job is not a snippet-only one: keep it
            note = "⚠️ The source shows only the start of the ad — open the original to read the rest (hours, German, tasks)"
            a["risks"] = [note] + [r for r in (a.get("risks") or []) if r != note][:2]
            self.db.update_job(job_id, analysis=a)
            return "keep"
        if has_student_signal(job.get("title") or "", job.get("description") or "", str(meta.get("employment_type") or "")):
            note = "⚠️ The source gave only a snippet of the ad — check hours and language on the original before applying"
            a["risks"] = [note] + [r for r in (a.get("risks") or []) if r != note][:2]
            self.db.update_job(job_id, analysis=a)
            return "keep"
        self.db.update_job(job_id, stage="blocked", reason="snippet_only: no full text, no part-time/student signal")
        return "blocked"

    async def _ai(self, coro):
        try:
            res = await coro
            if self.db.kv_get("ai_down_alerted"):
                self.db.kv_set("ai_down_alerted", False)
                await self.notifier.text("✅ The Claude API works again — job scoring has resumed.")
            return res
        except anthropic.BadRequestError as e:
            msg = str(e)
            if "credit balance" in msg.lower() or "billing" in msg.lower():
                await self._ai_alert("💳 The Claude API balance is used up — new jobs are not being scored. "
                                     "Top up: platform.claude.com → Settings → Billing. "
                                     "I'll score the missed jobs automatically afterwards.")
            raise AIUnavailable(msg) from e
        except anthropic.AuthenticationError as e:
            await self._ai_alert("🔑 The Claude API key does not work (wrong or deleted). A new key is needed.")
            raise AIUnavailable(str(e)) from e
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APITimeoutError) as e:
            raise AIUnavailable(str(e)) from e

    async def _ai_alert(self, text: str) -> None:
        if not self.db.kv_get("ai_down_alerted"):
            self.db.kv_set("ai_down_alerted", True)
            await self.notifier.text(text)

    async def deliver(self, job_id: int, silent: bool = False) -> None:
        job = self.db.get_job(job_id)
        msg_id = await self.notifier.send_job(job, silent=silent)
        if msg_id:
            self.db.update_job(job_id, stage="sent", tg_message_id=msg_id, sent_at=iso(now_utc()))
            a = job.get("analysis") or {}
            await self.sheets.safe("log_job", {"row": {
                "date": iso(now_utc()), "score": job.get("score"),
                "company": a.get("company_clean") or job.get("company"),
                "title": a.get("title_clean") or job.get("title"),
                "source": self.notifier.source_label(job),
                "url": job.get("apply_url") or job.get("url"),
                "why": " | ".join(a.get("why") or []), "risks": " | ".join(a.get("risks") or []),
                "job_id": job_id,
            }})

    async def resolve_company_link(self, job_id: int) -> None:
        """Background: find the posting on the company's own site and update the message."""
        job = self.db.get_job(job_id)
        if not job or classify_link(job.get("apply_url") or "") == "company":
            return
        try:
            found = await asyncio.to_thread(guess_ats_link, job["company"], job["title"])
        except Exception:  # noqa: BLE001
            found = None
        url = None
        if found:
            url, board = found
            kind, key, params = board
            if self.db.upsert_board(kind, key, job["company"], "learned", params):
                log.info("learned board via slug guess %s:%s", kind, key)
        elif (job.get("score") or 0) >= int(self.settings.get("links.search_min_score", 60)):
            cap = int(self.settings.get("links.search_daily_cap", 12))
            if self.db.count_usage_today("link_search") < cap:
                try:
                    url = await self.ai.find_company_posting(job["company"], job["title"], job.get("location") or "Vienna")
                except Exception as e:  # noqa: BLE001
                    log.info("link search failed: %s", e)
                if url and classify_link(url) != "company":
                    url = None
        if url:
            self.db.update_job(job_id, apply_url=url, apply_kind="company")
            job = self.db.get_job(job_id)
            meta = job.get("meta") or {}
            meta["company_link_found"] = True
            self.db.update_job(job_id, meta=meta)
            await self.notifier.refresh_job(job_id)

    # ------------------------------------------------------------------ maintenance
    async def retry_errors(self) -> int:
        """Re-evaluate jobs that failed (site hiccup, API outage) within the last 4 days.

        A job whose page could not be opened used to get 3 tries in ~15 minutes and was then written off for good —
        exactly when a site is down for an hour. Now: up to 8 tries with growing gaps (5, 10, 20, 40, 80, 160 min…),
        i.e. the ad survives an outage of several hours. Jobs that wait for the Claude API are not counted."""
        since = iso(now_utc() - timedelta(days=4))
        rows = self.db.query(
            "SELECT * FROM jobs WHERE stage IN ('error','queued') AND first_seen>=? AND updated<=? ORDER BY id LIMIT 300",
            (since, iso(now_utc() - timedelta(minutes=5))),
        )
        n = 0
        tried = 0
        li_cooling = time.time() < LinkedInSource.cooldown_until
        for row in rows:
            if tried >= 40:
                break
            job = self.db.row_to_job(row)
            if job["source"] == "linkedin" and li_cooling:
                continue  # LinkedIn is throttled: wait, do not spend an attempt (and an AI triage call)
            meta = job.get("meta") or {}
            if (job.get("reason") or "").startswith("AI:"):
                # Claude API down / out of credit: keep the job until the API is back (4-day window), try ≤ every 15 min
                if (parse_iso(job.get("updated")) or now_utc()) > now_utc() - timedelta(minutes=15):
                    continue
            else:
                attempts = int(meta.get("attempts", 0)) + 1
                if attempts > (12 if job["source"] == "linkedin" else 8):
                    self.db.update_job(job["id"], stage="failed")
                    continue
                gap = timedelta(minutes=min(5 * 2 ** max(attempts - 2, 0), 180)) if job["source"] != "linkedin" \
                    else timedelta(minutes=5)
                if (parse_iso(job.get("updated")) or now_utc()) > now_utc() - gap:
                    continue  # not yet: waiting out the back-off, no attempt is spent
                meta["attempts"] = attempts
            tried += 1
            self.db.update_job(job["id"], meta=meta, stage="queued")
            raw = RawJob(
                source=job["source"], external_id=job["external_id"], title=job["title"], company=job["company"],
                location=job.get("location") or "", url=job.get("url") or "", apply_url=job.get("apply_url") or "",
                description=job.get("description") or "", posted_at=parse_iso(job.get("posted_at")),
                company_linkedin=job.get("company_linkedin") or "", meta=meta,
                needs_detail=bool(meta.get("pending_detail")) or not bool(job.get("description")) and job["source"].split(":")[0] in
                ("linkedin", "karriere", "zbp", "smartrecruiters", "workday", "rmk", "jobsat", "willhaben", "derstandard",
                 "taleo", "htmllist", "bamboohr", "breezy", "join", "osce"),
            )
            name = "boards" if ":" in job["source"] else job["source"]
            if name not in self.sources:
                continue
            res = await self.evaluate(job["id"], raw, name, silent=bool(meta.get("initial")))
            n += res == "sent"
        return n

    async def release_held(self) -> int:
        rows = self.db.query("SELECT id FROM jobs WHERE stage='held' ORDER BY score DESC LIMIT 60")
        for r in rows:
            await self.deliver(r["id"], silent=True)
        return len(rows)
