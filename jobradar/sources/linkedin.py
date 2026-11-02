"""LinkedIn public (guest) job search — no login, no cookies.

Search:  /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=&location=...&f_TPR=r1800&start=0
Detail:  /jobs-guest/jobs/api/jobPosting/{id}

Note (Sept 2026): LinkedIn silently ignores f_JT/f_E/f_WT/sortBy for guests; only f_TPR (posted within)
and f_AL still filter, so we search broadly and filter on our side.

Note (Nov 2026, measured from a data-centre IP):
  * a guest search page holds 10 cards, so `start` must advance by 10 (it used to advance by 25 and skipped
    cards 10–24 of every block);
  * the limit is per request and random (≈ 20–30 % of requests got a 429, the next one usually passed), so a
    single 429 must not stop the source for an hour: every request goes through one global queue (one request
    at a time, 3–5 s apart, also for job details) and a 429 is retried after a pause before anything is paused.
"""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import clean_url, html_to_text, parse_iso, parse_relative_age
from .base import Source

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
VIEW = "https://www.linkedin.com/jobs/view/{id}/"


#: statuses that mean "slow down" (LinkedIn answers 429, sometimes 999 or 403)
_THROTTLE = (429, 999, 403)


class LinkedInBlocked(RuntimeError):
    pass


class LinkedInSource(Source):
    name = "linkedin"
    label = "LinkedIn"

    #: after repeated 429s we leave LinkedIn alone for a while (class-level: shared by all runs)
    cooldown_until: float = 0.0
    _rr: int = 0  # round-robin pointer over the "sweep" queries
    _strikes: int = 0  # pauses in a row (each within 2 h of the previous one)
    _day_runs: int = 0
    _last_throttle: float = 0.0
    #: ONE request at a time for the whole bot (searches and job details come from different threads) and a
    #: minimum gap between two requests: bursts of parallel detail requests were a main cause of the 429s
    _net_lock = threading.Lock()
    _next_ok: float = 0.0

    def _start_cooldown(self) -> int:
        """Pause after a request failed with 429 on every retry: 10 → 20 → 30 min while LinkedIn keeps refusing,
        back to 10 after 2 quiet hours. Called once per give-up (by `_get`), never for a single retried 429.

        History: a fixed 15-min pause after EVERY 429 hit the limit again right after each pause; the 15 → 60 min
        ladder (28.10.2026) then kept LinkedIn paused almost all day (14 pauses in a row on 01–02.11.2026), although
        a single 429 is random and the next request mostly passes."""
        now = time.time()
        cls = LinkedInSource
        cls._strikes = cls._strikes + 1 if now - cls._last_throttle < 7200 else 1
        cls._last_throttle = now
        base = int(self.params.get("cooldown_sec", 600))
        pause = min(base * 2 ** (cls._strikes - 1), int(self.params.get("cooldown_max_sec", 1800)))
        cls.cooldown_until = now + pause
        self.log.warning("LinkedIn throttled (#%d in a row) — pausing %d min", cls._strikes, pause // 60)
        return pause

    def _queries(self) -> list[dict]:
        return self.params.get("queries") or [{"keywords": "", "location": "Vienna, Vienna, Austria", "distance": 25}]

    def _pace(self) -> None:
        """Earliest moment for the next request: a random gap after the one that just finished."""
        lo, hi = self.params.get("delay_sec", (3.0, 5.5))
        LinkedInSource._next_ok = time.time() + random.uniform(lo, hi)

    def _get(self, url: str, kind: str = "search", ok: tuple = (200,)):
        """The only way to reach LinkedIn: one request at a time, paced, and a 429 is retried after a pause
        (25–50 s, then 50–100 s) before the source is paused. Raises LinkedInBlocked when it gives up or when
        another thread has already started a pause; other HTTP errors (404 …) are raised as they are."""
        cls = LinkedInSource
        retries = int(self.params.get("retries", 2))
        lo, hi = self.params.get("retry_wait_sec", (25, 50))
        with cls._net_lock:
            for attempt in range(retries + 1):
                if time.time() < cls.cooldown_until:
                    raise LinkedInBlocked("LinkedIn cooldown after throttling")
                wait = cls._next_ok - time.time()
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = http.get(url, ok=ok, headers={"Accept": "text/html"}, retry_429=0, retry_block=())
                except http.HttpError as e:
                    if e.status not in _THROTTLE:
                        self._pace()
                        raise
                    if attempt < retries:
                        pause = random.uniform(lo, hi) * (attempt + 1)
                        self.log.info("LinkedIn %s on a %s request — retry %d/%d in %.0f s",
                                      e.status, kind, attempt + 1, retries, pause)
                        cls._next_ok = time.time() + pause
                        continue
                    self._start_cooldown()
                    raise LinkedInBlocked(f"LinkedIn throttled ({e.status})") from e
                self._pace()
                if attempt:
                    self.log.info("LinkedIn %s request passed after %d retr%s", kind, attempt, "y" if attempt == 1 else "ies")
                return r
        raise LinkedInBlocked("LinkedIn throttled")  # unreachable: the loop returns or raises

    def _run_query(self, q: dict, window: int, max_pages: int, out: dict[str, RawJob]) -> int:
        """Paginate one search. A guest page holds 10 cards (`page_size`), so `start` advances by 10; LinkedIn
        sometimes returns fewer cards than that even when more pages exist, so we stop only on an empty page or
        a page without anything new."""
        added = 0
        step = int(self.params.get("page_size", 10))
        for page in range(max_pages):
            url = (
                f"{SEARCH}?keywords={quote(q.get('keywords', ''))}&location={quote(q.get('location', ''))}"
                f"&f_TPR=r{window}&start={page * step}"
            )
            if q.get("distance"):
                url += f"&distance={q['distance']}"
            if q.get("geoId"):
                url += f"&geoId={q['geoId']}"
            cards = self._get_cards(url)
            new = 0
            for job in cards:
                if q.get("require_remote"):
                    job.meta["query_remote"] = True
                if job.external_id not in out:
                    out[job.external_id] = job
                    new += 1
            added += new
            if not cards or not new:
                break
        return added

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        if time.time() < LinkedInSource.cooldown_until and not backfill:
            return []
        out: dict[str, RawJob] = {}
        queries = self._queries()
        main = [q for q in queries if not q.get("sweep")]
        sweep = [q for q in queries if q.get("sweep")]
        try:
            for q in main:
                if backfill:
                    window, pages = int(self.params.get("backfill_window_sec", 604800)), int(self.params.get("backfill_max_pages", 20))
                else:
                    window = int(q.get("window_sec", self.params.get("window_sec", 10800)))
                    pages = int(q.get("max_pages", self.params.get("max_pages", 8)))
                self._run_query(q, window, pages, out)
            # each run also does the next 1–2 keyword "sweep" searches (24 h window) — catches what the broad
            # search misses (LinkedIn ranks by relevance and hides part of the results for empty keywords)
            if sweep and not backfill:
                # LinkedIn's guest limit is strict in the daytime (28–29.10: blocked ~8 h in a row from 07:00,
                # no throttling at all at night) → keyword sweeps (24 h window) run mostly at night, the
                # daytime budget goes to the fresh main search and to opening new jobs.
                hour = datetime.now(ZoneInfo("Europe/Vienna")).hour
                start, end = self.params.get("sweep_night", [20, 7])
                night = hour >= int(start) or hour < int(end)
                n_sweeps = int(self.params.get("sweep_per_run", 1)) if night else 0
                if not night and int(self.params.get("day_sweep_every", 0) or 0):
                    LinkedInSource._day_runs += 1
                    if LinkedInSource._day_runs % int(self.params["day_sweep_every"]) == 0:
                        n_sweeps = 1
                for _ in range(n_sweeps):
                    q = sweep[LinkedInSource._rr % len(sweep)]
                    LinkedInSource._rr += 1
                    self._run_query(q, int(q.get("window_sec", 86400)), int(q.get("max_pages", 4)), out)
        except LinkedInBlocked:
            # the pause was already started by `_get` (once per give-up) — here we only keep what we got
            if out:  # keep what we already got; the next runs will resume after the cooldown
                self.log.warning("LinkedIn throttled — cooling down; keeping %d cards", len(out))
                return list(out.values())
            raise
        return list(out.values())

    def _get_cards(self, url: str) -> list[RawJob]:
        r = self._get(url, "search", ok=(200, 400))
        if r.status_code == 400 or not r.text.strip():
            return []
        soup = BeautifulSoup(r.text, "lxml")
        jobs: list[RawJob] = []
        for li in soup.select("li"):
            urn_el = li.select_one("[data-entity-urn]")
            urn = urn_el.get("data-entity-urn", "") if urn_el else ""
            m = re.search(r"jobPosting:(\d+)", urn)
            if not m:
                a = li.select_one("a.base-card__full-link")
                m = re.search(r"-(\d{8,})", a.get("href", "")) if a else None
            if not m:
                continue
            jid = m.group(1)
            title_el = li.select_one(".base-search-card__title")
            comp_el = li.select_one(".base-search-card__subtitle")
            loc_el = li.select_one(".job-search-card__location")
            time_el = li.select_one("time")
            comp_link = comp_el.select_one("a") if comp_el else None
            posted = None
            if time_el is not None:
                posted = parse_relative_age(time_el.get_text(" ", strip=True)) or parse_iso(time_el.get("datetime"))
            benefits = li.select_one(".job-posting-benefits__text")
            jobs.append(
                RawJob(
                    source="linkedin",
                    external_id=jid,
                    title=title_el.get_text(" ", strip=True) if title_el else "",
                    company=comp_el.get_text(" ", strip=True) if comp_el else "",
                    location=loc_el.get_text(" ", strip=True) if loc_el else "",
                    url=VIEW.format(id=jid),
                    posted_at=posted,
                    company_linkedin=_canon_company_url(comp_link.get("href", "")) if comp_link else "",
                    meta={"benefits": benefits.get_text(" ", strip=True) if benefits else ""},
                    needs_detail=True,
                )
            )
        return jobs

    def fetch_detail(self, job: RawJob) -> RawJob:
        if time.time() < LinkedInSource.cooldown_until:
            raise LinkedInBlocked("LinkedIn cooldown after throttling")
        r = self._get(DETAIL.format(id=job.external_id), "detail")
        soup = BeautifulSoup(r.text, "lxml")
        desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
        job.description = html_to_text(str(desc)) if desc else job.description
        criteria = {}
        for li in soup.select("li.description__job-criteria-item"):
            h = li.select_one("h3")
            v = li.select_one("span")
            if h and v:
                criteria[h.get_text(strip=True)] = v.get_text(" ", strip=True)
        job.meta["criteria"] = criteria
        appl = soup.select_one(".num-applicants__caption") or soup.select_one(".num-applicants__figure")
        if appl:
            job.meta["applicants"] = appl.get_text(" ", strip=True)
        job.meta["offsite_apply"] = "offsite-apply-icon" in r.text
        job.meta["easy_apply"] = not job.meta["offsite_apply"]
        comp = soup.select_one("a.topcard__org-name-link")
        if comp and not job.company_linkedin:
            job.company_linkedin = _canon_company_url(comp.get("href", ""))
        if job.meta.get("from_alert"):  # title/company were parsed from an e-mail → take LinkedIn's own
            t = soup.select_one(".top-card-layout__title") or soup.select_one(".topcard__title")
            if t and t.get_text(strip=True):
                job.title = t.get_text(" ", strip=True)
            if comp and comp.get_text(strip=True):
                job.company = comp.get_text(" ", strip=True)
            loc = soup.select_one(".topcard__flavor--bullet")
            if loc and loc.get_text(strip=True):
                job.location = loc.get_text(" ", strip=True)
        job.needs_detail = False
        return job


def _canon_company_url(href: str) -> str:
    m = re.search(r"linkedin\.com/company/([^/?#]+)", href or "")
    return f"https://www.linkedin.com/company/{m.group(1)}" if m else clean_url(href or "")
