"""Remote-job boards and automatic discovery of remote-first employers (v1.2.0).

Why: many remote jobs for people living in Vienna (fintechs in Mexico staffed from Europe, SaaS and crypto firms
hiring "EMEA/Worldwide") are not on karriere.at / LinkedIn Vienna searches, only on the employers' own career sites.
Public remote-job boards list them with the employer's name; this source reads them, keeps only business-type
titles that can be done from Austria, and — the important part — teaches the bot the employers' own boards
(Greenhouse / Lever / Ashby / Recruitee / Workable / SmartRecruiters), so the company is then polled directly.

Sources (all public, no key):
  remotive      remotive.com/api           (free API is delayed 24 h and rate-limited → every 6 h)
  remoteok      remoteok.com/api           (JSON; first element is a legal notice)
  wwr           weworkremotely.com RSS     (item <region> says where the job can be done from)
  himalayas     himalayas.app/jobs/api/search?country=AT   (filters by country server-side; full description)
  jobicy        jobicy.com/api/v2/remote-jobs
  workingnomads workingnomads.com/api/exposed_jobs
"""
from __future__ import annotations

import html as _html
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_iso
from .base import Source
from .boards import ADAPTERS, detect_board, region_ok, role_ok

SITE_INTERVAL = {"remotive": 21600, "remoteok": 3600, "wwr": 3600, "himalayas": 1800, "jobicy": 3600,
                 "workingnomads": 3600}
HIMALAYAS_QUERIES = [
    "business development", "sales", "account executive", "customer success", "analyst", "finance", "marketing",
    "operations", "partnerships", "intern", "junior", "assistant", "research", "compliance", "recruiter",
    "project coordinator", "growth", "product analyst", "fintech", "risk", "consultant", "chief of staff",
]
_HREF = re.compile(r"""href=["'](https?://[^"'\s>]+)""", re.I)
_BARE_URL = re.compile(r"https?://[^\s\"'<>)]+", re.I)
_SUFFIX = re.compile(r"\b(gmbh|ag|inc|incorporated|ltd|limited|llc|llp|plc|corp|corporation|co|sa|s\.a\.|bv|b\.v\.|"
                     r"oy|ab|as|srl|sas|se|kg|ug|pte|pty|holdings?|group|technologies|technology|labs?)\b\.?", re.I)


def _dt(value) -> datetime | None:
    if value in (None, "", "None"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        if isinstance(value, str) and re.match(r"^[A-Z][a-z]{2},", value):  # RFC 822 (RSS)
            return parsedate_to_datetime(value).astimezone(timezone.utc)
    except (ValueError, OSError, TypeError):
        return None
    dt = parse_iso(str(value))
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ats_url(*chunks: str) -> str:
    """First link in the given html/text that is an applicant-tracking-system posting (Greenhouse, Lever ...)."""
    for chunk in chunks:
        if not chunk:
            continue
        for u in _HREF.findall(chunk) + _BARE_URL.findall(chunk):
            u = _html.unescape(u).rstrip(".,;")
            if detect_board(u):
                return u
    return ""


def _job(site: str, ext_id: str, title: str, company: str, region: str, url: str, desc_html: str,
         posted, job_type: str = "", extra: dict | None = None) -> RawJob:
    desc_html = desc_html or ""
    head = [f"Source: {site} (remote job board)"]
    if job_type:
        head.append(f"Employment type: {job_type}")
    head.append(f"Where the job can be done from: {region or 'not stated'}")
    text = "\n".join(head) + "\n\n" + html_to_text(desc_html)
    ats = _ats_url(desc_html, url)
    loc = "Remote" + (f" ({region})" if region else "")
    return RawJob(
        source="remotejobs", external_id=f"{site}:{ext_id}", title=_html.unescape(title or "").strip(),
        company=_html.unescape(company or "").strip(), location=loc[:100], url=url, apply_url=ats or url,
        description=text, posted_at=_dt(posted),
        meta={"remote_site": site, "workplace": "remote", "region": region or "", **(extra or {})},
    )


# ---------------------------------------------------------------- site adapters (pure parsing + one request)
def parse_remotive(data: dict) -> list[RawJob]:
    out = []
    for j in data.get("jobs", []):
        region = j.get("candidate_required_location") or ""
        if not region_ok(region):
            continue
        out.append(_job("remotive", str(j["id"]), j.get("title"), j.get("company_name"), region, j.get("url", ""),
                        j.get("description"), j.get("publication_date"), (j.get("job_type") or "").replace("_", " ")))
    return out


def parse_remoteok(data: list) -> list[RawJob]:
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is the legal notice
        region = j.get("location") or ""
        if not region_ok(region):
            continue
        url = j.get("url") or j.get("apply_url") or ""
        out.append(_job("remoteok", str(j.get("id") or j.get("slug")), j["position"], j.get("company"), region, url,
                        j.get("description"), j.get("date") or j.get("epoch")))
    return out


def parse_wwr(xml: str) -> list[RawJob]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
            return _html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else ""

        title, region = tag("title"), tag("region")
        country = tag("country")
        place = ", ".join(x for x in (region, country) if x)
        if not region_ok(place):
            continue
        company, _, role = title.partition(": ")
        if not role:
            company, role = "", title
        link = tag("link") or tag("guid")
        out.append(_job("wwr", re.sub(r"\W+", "-", tag("guid") or link)[-80:], role, company, place, link,
                        tag("description"), tag("pubDate"), tag("type")))
    return out


def parse_himalayas(data: dict) -> list[RawJob]:
    out = []
    for j in data.get("jobs", []):
        restr = j.get("locationRestrictions") or []
        if not region_ok(restr):
            continue
        region = "Worldwide" if not restr else ("Europe" if len(restr) > 6 else ", ".join(restr))
        link = j.get("applicationLink") or j.get("guid") or ""
        out.append(_job("himalayas", j.get("guid") or link, j.get("title"), j.get("companyName"), region, link,
                        j.get("description") or j.get("excerpt"), j.get("pubDate"), j.get("employmentType") or "",
                        {"seniority": j.get("seniority")}))
    return out


def parse_jobicy(data: dict) -> list[RawJob]:
    out = []
    for j in data.get("jobs", []):
        geo = j.get("jobGeo") or ""
        if not region_ok(geo):
            continue
        jt = j.get("jobType")
        jt = ", ".join(jt) if isinstance(jt, list) else (jt or "")
        out.append(_job("jobicy", str(j["id"]), j.get("jobTitle"), j.get("companyName"), geo, j.get("url", ""),
                        j.get("jobDescription") or j.get("jobExcerpt"), j.get("pubDate"), jt))
    return out


def parse_workingnomads(data: list) -> list[RawJob]:
    out = []
    for j in data:
        loc = j.get("location") or ""
        if not region_ok(loc.split(",")):  # a list of allowed countries: ok when Austria/Europe is among them
            continue
        out.append(_job("workingnomads", re.sub(r"\D", "", j.get("url", ""))[-12:] or j.get("url", ""),
                        j.get("title"), j.get("company_name"), "Worldwide" if not loc else loc[:60], j.get("url", ""),
                        j.get("description"), j.get("pub_date")))
    return out


# ---------------------------------------------------------------- discovery of the employer's own board
def name_key(name: str) -> str:
    n = _SUFFIX.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def slug_candidates(name: str) -> list[str]:
    base = _SUFFIX.sub(" ", re.sub(r"\(.*?\)", " ", (name or "").lower()))
    words = [w for w in re.split(r"[^a-z0-9]+", base) if w]
    if not words:
        return []
    cands = ["".join(words), "-".join(words)]
    if len(words) > 1 and len(words[0]) >= 4:
        cands.append(words[0])
    seen, out = set(), []
    for c in cands:
        if c and len(c) >= 3 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


_PROBE_KINDS = ("greenhouse", "lever", "ashby", "recruitee", "workable", "smartrecruiters")


def _title_norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def discover_board(company: str, sample_title: str, db, log) -> tuple[str, str] | None:
    """Look for `company`'s own board among the common ATS. A board is accepted only when the job we saw on a remote
    board is on it with the same title (so a different company that happens to own the slug is never taken), or —
    Greenhouse only — when the board's own name equals the company's. Returns (kind, key) and stores it."""
    want = _title_norm(sample_title)
    for slug in slug_candidates(company):
        for kind in _PROBE_KINDS:
            fn = ADAPTERS.get(kind)
            if not fn:
                continue
            try:
                jobs = fn(slug, {"no_location_filter": True, "eu": False}, company)
            except Exception:  # noqa: BLE001 - a missing board is the normal answer
                continue
            if not jobs:
                continue
            titles = {_title_norm(j.title) for j in jobs}
            matched = want in titles or any(want and (want in t or t in want) and min(len(want), len(t)) > 12
                                            for t in titles)
            if not matched and kind == "greenhouse":
                try:
                    meta = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                    matched = name_key(meta.get("name", "")) == name_key(company)
                except Exception:  # noqa: BLE001
                    matched = False
            if matched:
                params = {"remote_first": True, "min_interval_sec": 1800}
                if db.upsert_board(kind, slug, company, "remote-learned", params):
                    log.info("remote discovery: new board %s:%s for %s (%d jobs)", kind, slug, company, len(jobs))
                return kind, slug
    return None


# ---------------------------------------------------------------- source
class RemoteJobsSource(Source):
    name = "remotejobs"
    label = "Remote job boards"

    def __init__(self, settings, params=None, db=None):
        super().__init__(settings, params)
        self.db = db

    # one request per site and run; the per-site pause is kept in the DB so a restart does not hammer anybody
    def _due(self, site: str) -> bool:
        if self.db is None:
            return True
        every = int((self.params.get("intervals") or {}).get(site, SITE_INTERVAL.get(site, 3600)))
        last = float(self.db.kv_get(f"remote_last:{site}", 0) or 0)
        return time.time() - last >= every

    def _done(self, site: str) -> None:
        if self.db is not None:
            self.db.kv_set(f"remote_last:{site}", time.time())

    def _himalayas(self) -> list[RawJob]:
        qs = self.params.get("himalayas_queries") or HIMALAYAS_QUERIES
        n = int(self.params.get("himalayas_per_run", 4))
        pos = int(self.db.kv_get("remote_rr:himalayas", 0) or 0) if self.db else 0
        picked = [qs[(pos + i) % len(qs)] for i in range(n)]
        if self.db:
            self.db.kv_set("remote_rr:himalayas", (pos + n) % len(qs))
        out = []
        for q in picked:
            data = http.get_json("https://himalayas.app/jobs/api/search", params={"q": q, "country": "AT", "sort": "recent"})
            out.extend(parse_himalayas(data))
        return out

    def _fetch_site(self, site: str) -> list[RawJob]:
        if site == "remotive":
            return parse_remotive(http.get_json("https://remotive.com/api/remote-jobs"))
        if site == "remoteok":
            return parse_remoteok(http.get_json("https://remoteok.com/api"))
        if site == "wwr":
            return parse_wwr(http.get("https://weworkremotely.com/remote-jobs.rss", ok=(200,)).text)
        if site == "himalayas":
            return self._himalayas()
        if site == "jobicy":
            out = []
            for geo in ("anywhere", "europe"):
                out.extend(parse_jobicy(http.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "geo": geo})))
            return out
        if site == "workingnomads":
            return parse_workingnomads(http.get_json("https://www.workingnomads.com/api/exposed_jobs/"))
        return []

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        self.begin()
        sites = self.params.get("sites") or list(SITE_INTERVAL)
        out: list[RawJob] = []
        ran = 0
        for site in sites:
            if not self._due(site):
                continue
            try:
                got = self._fetch_site(site)
                self._done(site)
                ran += 1
            except Exception as e:  # noqa: BLE001 - one site down must not stop the others
                self.note_error(e, site)
                continue
            kept = [j for j in got if role_ok(j.title)]
            self.log.info("remote %s: %d jobs, %d business-type", site, len(got), len(kept))
            out.extend(kept)
        seen: set[str] = set()
        uniq = []
        for j in out:  # the same job often sits on several boards
            k = f"{name_key(j.company)}|{_title_norm(j.title)}"
            if k not in seen:
                seen.add(k)
                uniq.append(j)
        if self.db is not None and not backfill:
            self._discover(uniq)
        if ran == 0 and not getattr(self, "errors", []):
            return []  # nothing was due
        return self.finish(uniq)

    def _discover(self, jobs: list[RawJob]) -> None:
        """Companies of today's relevant jobs → their own ATS board (a few per run; each company is tried again
        only after `discover_retry_days`)."""
        limit = int(self.params.get("discover_per_run", 6))
        retry = float(self.params.get("discover_retry_days", 10)) * 86400
        known = {(b["company"] or "").lower() for b in self.db.boards(enabled_only=False)}
        tried = 0
        for j in jobs:
            if tried >= limit:
                break
            comp = (j.company or "").strip()
            if not comp or comp.lower() in known or len(name_key(comp)) < 3:
                continue
            key = f"remote_probe:{name_key(comp)}"
            last = float(self.db.kv_get(key, 0) or 0)
            if last and time.time() - last < retry:
                continue
            tried += 1
            self.db.kv_set(key, time.time())
            if j.apply_url:
                b = detect_board(j.apply_url)  # the ad itself links to the employer's ATS → no guessing needed
                if b and b[0] in ADAPTERS:
                    kind, bkey, bp = b
                    if self.db.upsert_board(kind, bkey, comp, "remote-learned", {**bp, "remote_first": True, "min_interval_sec": 1800}):
                        self.log.info("remote discovery: new board %s:%s for %s (linked from the ad)", kind, bkey, comp)
                    known.add(comp.lower())
                    continue
            try:
                found = discover_board(comp, j.title, self.db, self.log)
            except Exception as e:  # noqa: BLE001
                self.log.warning("remote discovery for %s failed: %s", comp, e)
                found = None
            if found:
                known.add(comp.lower())
