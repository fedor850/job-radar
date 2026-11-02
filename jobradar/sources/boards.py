"""Company career sites via their applicant-tracking systems (ATS).

Each adapter lists a company's open postings from a public JSON/XML endpoint and returns RawJobs
already filtered to Vienna/Austria-remote. `detect_board(url)` recognises ATS URLs so the radar can
learn new company boards automatically from links seen on Indeed / karriere.at / LinkedIn.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text, location_ok, parse_iso, parse_relative_age, VIENNA_RE
from .base import Source

log = logging.getLogger("jobradar.boards")

KIND_LABEL = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "workday": "Workday",
    "personio": "Personio",
    "recruitee": "Recruitee",
    "workable": "Workable",
    "rmk": "SuccessFactors",
    "amazon": "Amazon Jobs",
}


def _ms(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


_REMOTE_OK = re.compile(r"austria|österreich|osterreich|europe|emea|dach|\beu\b|cee", re.I)
_REMOTE_ONLY = re.compile(r"^\s*(remote|remote job|fully remote|anywhere|work from home|homeoffice)\s*$", re.I)


def _keep(location: str, text: str = "", remote: bool = False, params: dict | None = None) -> bool:
    """Vienna area, or remote that is clearly workable from Austria (Austria/Europe/EMEA/DACH or plain 'Remote')."""
    params = params or {}
    if params.get("no_location_filter"):
        return True
    if params.get("no_remote"):  # global employers with hundreds of "Remote" (US/anywhere) ads: Vienna only
        return bool(VIENNA_RE.search(location or ""))
    if location_ok(location, text):
        return True
    loc = (location or "").strip()
    is_remote = remote or bool(re.search(r"remote|home\s*office|anywhere", loc, re.I))
    if is_remote and (_REMOTE_OK.search(loc) or _REMOTE_ONLY.match(loc) or not loc):
        return True
    return False


# ---------------------------------------------------------------- adapters


def greenhouse(key: str, params: dict, company: str) -> list[RawJob]:
    host = "boards-api.eu.greenhouse.io" if params.get("eu") else "boards-api.greenhouse.io"
    data = http.get_json(f"https://{host}/v1/boards/{key}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "") or ""
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [])
        content = html_to_text(j.get("content") or "")
        if not _keep(f"{loc} {offices}", "", "remote" in loc.lower(), params):
            continue
        out.append(
            RawJob(
                source=f"greenhouse:{key}",
                external_id=str(j["id"]),
                title=j.get("title", ""),
                company=company or key,
                location=loc,
                url=j.get("absolute_url", ""),
                apply_url=j.get("absolute_url", ""),
                description=content,
                posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
            )
        )
    return out


def lever(key: str, params: dict, company: str) -> list[RawJob]:
    host = "api.eu.lever.co" if params.get("eu") else "api.lever.co"
    data = http.get_json(f"https://{host}/v0/postings/{key}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locs = " / ".join([cats.get("location") or ""] + list(cats.get("allLocations") or []))
        remote = (j.get("workplaceType") or "").lower() == "remote"
        if not _keep(locs, "", remote, params):
            continue
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(f"\n{lst.get('text','')}\n{html_to_text(lst.get('content',''))}")
        parts.append(j.get("additionalPlain") or "")
        out.append(
            RawJob(
                source=f"lever:{key}",
                external_id=j["id"],
                title=j.get("text", ""),
                company=company or key,
                location=cats.get("location") or locs,
                url=j.get("hostedUrl", ""),
                apply_url=j.get("hostedUrl", ""),
                description="\n".join(parts).strip(),
                posted_at=_ms(j.get("createdAt")),
                meta={"commitment": cats.get("commitment", ""), "workplace": j.get("workplaceType", "")},
            )
        )
    return out


def ashby(key: str, params: dict, company: str) -> list[RawJob]:
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{key}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        sec = " / ".join(s.get("location", "") for s in j.get("secondaryLocations") or [])
        loc = " / ".join(x for x in [j.get("location", ""), addr.get("addressLocality", ""), addr.get("addressCountry", ""), sec] if x)
        if not _keep(loc, "", bool(j.get("isRemote")), params):
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(
            RawJob(
                source=f"ashby:{key}",
                external_id=j["id"],
                title=j.get("title", ""),
                company=company or key,
                location=loc,
                url=j.get("jobUrl", ""),
                apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                posted_at=parse_iso(j.get("publishedAt")),
                meta={"employment_type": j.get("employmentType", ""), "workplace": j.get("workplaceType", ""), "salary": comp},
            )
        )
    return out


def smartrecruiters(key: str, params: dict, company: str) -> list[RawJob]:
    out = []
    offset = 0
    country = params.get("country", "at")
    while True:
        data = http.get_json(
            f"https://api.smartrecruiters.com/v1/companies/{key}/postings?limit=100&offset={offset}&country={country}"
        )
        content = data.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            loc_s = loc.get("fullLocation") or ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            if not _keep(loc_s, "", bool(loc.get("remote")), params):
                continue
            out.append(
                RawJob(
                    source=f"smartrecruiters:{key}",
                    external_id=str(j["id"]),
                    title=j.get("name", ""),
                    company=company or (j.get("company") or {}).get("name", key),
                    location=loc_s,
                    url=f"https://jobs.smartrecruiters.com/{key}/{j['id']}",
                    apply_url=f"https://jobs.smartrecruiters.com/{key}/{j['id']}",
                    posted_at=parse_iso(j.get("releasedDate")),
                    meta={
                        "employment_type": (j.get("typeOfEmployment") or {}).get("label", ""),
                        "level": (j.get("experienceLevel") or {}).get("label", ""),
                        "board_kind": "smartrecruiters",
                        "board_key": key,
                    },
                    needs_detail=True,
                )
            )
        offset += 100
        if offset >= int(data.get("totalFound", 0)) or not content or offset > 1000:
            break
    return out


def smartrecruiters_detail(job: RawJob) -> RawJob:
    key = job.meta.get("board_key") or job.source.split(":", 1)[1]
    d = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{key}/postings/{job.external_id}")
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    parts = []
    for name in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        sec = sections.get(name) or {}
        if sec.get("text"):
            parts.append(f"{sec.get('title','')}\n{html_to_text(sec['text'])}")
    job.description = "\n\n".join(parts)
    job.apply_url = d.get("applyUrl") or d.get("postingUrl") or job.apply_url
    job.url = d.get("postingUrl") or job.url
    job.needs_detail = False
    return job


def workday(key: str, params: dict, company: str) -> list[RawJob]:
    host, tenant, site = params["host"], params["tenant"], params["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen: dict[str, RawJob] = {}
    for text in params.get("search_texts") or ["Vienna", "Wien"]:
        data = http.post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": text})
        for j in data.get("jobPostings", []) or []:
            path = j.get("externalPath") or ""
            if not path or path in seen:
                continue
            loc = j.get("locationsText") or ""
            multi = bool(re.match(r"\d+\s+locations?", loc, re.I)) or not loc.strip()
            if not multi and not _keep(loc, "", False, params):
                continue
            seen[path] = RawJob(
                source=f"workday:{key}",
                external_id=path.rsplit("_", 1)[-1] if "_" in path else path,
                title=j.get("title", ""),
                company=company or tenant,
                location=loc,
                url=f"https://{host}/{site}{path}" if not params.get("lang") else f"https://{host}/{params['lang']}/{site}{path}",
                posted_at=parse_relative_age(j.get("postedOn", "")),
                meta={"board_kind": "workday", "wd_detail": f"{base}{path}", "multi_location": multi},
                needs_detail=True,
            )
    return list(seen.values())


def workday_detail(job: RawJob) -> RawJob:
    d = http.get_json(job.meta["wd_detail"]).get("jobPostingInfo", {})
    job.description = html_to_text(d.get("jobDescription", ""))
    locs = [d.get("location") or ""] + list(d.get("additionalLocations") or [])
    job.location = " / ".join(x for x in locs if x) or job.location
    job.url = d.get("externalUrl") or job.url
    job.apply_url = job.url
    job.meta["time_type"] = d.get("timeType", "")
    job.meta["start_date"] = d.get("startDate", "")
    if d.get("postedOn"):
        job.posted_at = parse_relative_age(d["postedOn"]) or job.posted_at
    job.needs_detail = False
    if job.meta.get("multi_location") and not location_ok(job.location):
        job.meta["drop"] = True  # multi-location posting without Vienna
    return job


def personio(key: str, params: dict, company: str) -> list[RawJob]:
    tld = params.get("tld", "de")
    r = http.get(f"https://{key}.jobs.personio.{tld}/xml?language=en")
    root = ET.fromstring(r.content)
    out = []
    for p in root.findall("position"):
        def g(tag: str) -> str:
            el = p.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        offices = [g("office")] + [(o.text or "") for o in p.findall("additionalOffices/office")]
        loc = " / ".join(o for o in offices if o)
        if not _keep(loc, "", False, params):
            continue
        desc_parts = []
        for jd in p.findall("jobDescriptions/jobDescription"):
            desc_parts.append(f"{(jd.findtext('name') or '').strip()}\n{html_to_text(jd.findtext('value') or '')}")
        pid = g("id")
        url = f"https://{key}.jobs.personio.{tld}/job/{pid}"
        out.append(
            RawJob(
                source=f"personio:{key}",
                external_id=pid,
                title=g("name"),
                company=company or g("subcompany") or key,
                location=loc,
                url=url,
                apply_url=url,
                description="\n\n".join(desc_parts),
                posted_at=parse_iso(g("createdAt")),
                meta={"schedule": g("schedule"), "employment_type": g("employmentType"), "seniority": g("seniority")},
            )
        )
    return out


def recruitee(key: str, params: dict, company: str) -> list[RawJob]:
    data = http.get_json(f"https://{key}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        loc = " / ".join(x for x in [j.get("location") or "", j.get("city") or "", j.get("country") or ""] if x)
        if not _keep(loc, "", bool(j.get("remote")), params):
            continue
        hours = ""
        if j.get("min_hours") or j.get("max_hours"):
            hours = f"{j.get('min_hours') or ''}-{j.get('max_hours') or ''} h/week"
        sal = j.get("salary") or {}
        salary = ""
        if sal.get("min") or sal.get("max"):
            salary = f"{sal.get('min') or ''}–{sal.get('max') or ''} {sal.get('currency') or ''} / {sal.get('period') or ''}"
        out.append(
            RawJob(
                source=f"recruitee:{key}",
                external_id=str(j["id"]),
                title=j.get("title", ""),
                company=company or j.get("company_name") or key,
                location=loc,
                url=j.get("careers_url", ""),
                apply_url=j.get("careers_apply_url") or j.get("careers_url", ""),
                description=html_to_text((j.get("description") or "") + (j.get("requirements") or "")),
                posted_at=parse_iso(j.get("published_at") or j.get("created_at")),
                meta={"hours": hours, "salary": salary, "employment_type": j.get("employment_type_code", "")},
            )
        )
    return out


def workable(key: str, params: dict, company: str) -> list[RawJob]:
    data = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{key}?details=true")
    out = []
    for j in data.get("jobs", []) or []:
        locs = [", ".join(x for x in [j.get("city"), j.get("country")] if x)]
        for l in j.get("locations") or []:
            locs.append(", ".join(x for x in [l.get("city"), l.get("country")] if x))
        loc = " / ".join(x for x in locs if x)
        if not _keep(loc, "", bool(j.get("telecommuting")), params):
            continue
        out.append(
            RawJob(
                source=f"workable:{key}",
                external_id=j.get("shortcode") or j.get("code") or j.get("url", ""),
                title=j.get("title", ""),
                company=company or data.get("name") or key,
                location=loc,
                url=j.get("url") or j.get("shortlink", ""),
                apply_url=j.get("application_url") or j.get("url", ""),
                description=html_to_text(j.get("description", "")),
                posted_at=parse_iso(j.get("published_on") or j.get("created_at")),
                meta={"employment_type": j.get("employment_type", "")},
            )
        )
    return out


def rmk(key: str, params: dict, company: str) -> list[RawJob]:
    """SAP SuccessFactors 'Recruiting Marketing' career sites (e.g. careers.ey.com/ey, jobs.rbinternational.com)."""
    host = params["host"]
    prefix = params.get("prefix", "")
    loc = params.get("locationsearch", "Wien")
    url = (
        f"https://{host}{prefix}/search/?q=&locationsearch={loc}&sortColumn=referencedate&sortDirection=desc"
        f"&startrow=0"
    )
    r = http.get(url, retry_block=(403,))  # SuccessFactors sites answer 403 / 429 at random
    soup = BeautifulSoup(r.text, "lxml")
    out: dict[str, RawJob] = {}
    for row in soup.select("tr.data-row, li.job-tile, div.job-tile"):
        a = row.select_one("a.jobTitle-link, a[href*='/job/']")
        if not a:
            continue
        href = urljoin(f"https://{host}", a.get("href", ""))
        m = re.search(r"/(\d{6,})/?$", href)
        if not m:
            continue
        loc_el = row.select_one(".jobLocation, [data-col='location'], .location")
        loc_s = loc_el.get_text(" ", strip=True) if loc_el else ""
        date_el = row.select_one(".jobDate, [data-col='date'], .date")
        if not _keep(loc_s or href, "", False, params) and not VIENNA_RE.search(href):
            continue
        out[m.group(1)] = RawJob(
            source=f"rmk:{key}",
            external_id=m.group(1),
            title=a.get_text(" ", strip=True),
            company=company or key,
            location=loc_s,
            url=href,
            apply_url=href,
            posted_at=_parse_rmk_date(date_el.get_text(" ", strip=True) if date_el else ""),
            meta={"board_kind": "rmk"},
            needs_detail=True,
        )
    if not out:  # fallback: plain links on the page
        for a in soup.select("a[href*='/job/']"):
            href = urljoin(f"https://{host}", a.get("href", ""))
            m = re.search(r"/(\d{6,})/?$", href)
            if m and (VIENNA_RE.search(href) or params.get("no_location_filter")):
                out.setdefault(
                    m.group(1),
                    RawJob(source=f"rmk:{key}", external_id=m.group(1), title=a.get_text(" ", strip=True),
                           company=company or key, url=href, apply_url=href, meta={"board_kind": "rmk"},
                           needs_detail=True),
                )
    return list(out.values())


def _parse_rmk_date(s: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%d.%m.%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def rmk_detail(job: RawJob) -> RawJob:
    r = http.get(job.url, retry_block=(403,))
    soup = BeautifulSoup(r.text, "lxml")
    desc = soup.select_one("span.jobdescription, [itemprop='description'], .job-description, .jobDisplay")
    job.description = html_to_text(str(desc)) if desc else html_to_text(r.text, 8000)
    loc = soup.select_one("[itemprop='jobLocation'], .jobGeoLocation, [data-careersite-propertyid='city']")
    if loc:
        job.location = loc.get_text(" ", strip=True)
    title = soup.select_one("[itemprop='title'], h1")
    if title and not job.title:
        job.title = title.get_text(" ", strip=True)
    dp = soup.select_one("[itemprop='datePosted']")
    if dp:
        job.posted_at = parse_iso(dp.get("content")) or _parse_rmk_date(dp.get_text(strip=True)) or job.posted_at
    job.needs_detail = False
    return job


def amazon(key: str, params: dict, company: str) -> list[RawJob]:
    """amazon.jobs public search JSON (Amazon / AWS)."""
    loc = params.get("loc_query", "Vienna, Austria")
    data = http.get_json(
        f"https://www.amazon.jobs/en/search.json?loc_query={quote(loc)}&country={params.get('country', 'AUT')}"
        f"&result_limit=100&sort=recent"
    )
    out = []
    for j in data.get("jobs", []) or []:
        city = f"{j.get('city','')} {j.get('location','')}"
        if not VIENNA_RE.search(city):
            continue
        desc = "\n\n".join(html_to_text(j.get(k) or "") for k in ("description", "basic_qualifications", "preferred_qualifications"))
        posted = None
        try:
            posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        url = "https://www.amazon.jobs" + (j.get("job_path") or "")
        out.append(RawJob(source=f"amazon:{key}", external_id=str(j.get("id_icims") or j.get("id")), title=j.get("title", ""),
                          company=j.get("company_name") or company or "Amazon", location=j.get("location", ""), url=url,
                          apply_url=url, description=desc, posted_at=posted,
                          meta={"job_type": j.get("job_schedule_type", "")}))
    return out


ADAPTERS: dict[str, Callable[[str, dict, str], list[RawJob]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "personio": personio,
    "recruitee": recruitee,
    "workable": workable,
    "rmk": rmk,
    "amazon": amazon,
}
DETAILS: dict[str, Callable[[RawJob], RawJob]] = {
    "smartrecruiters": smartrecruiters_detail,
    "workday": workday_detail,
    "rmk": rmk_detail,
}


# ---------------------------------------------------------------- URL → board detection

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_\-]+)", re.I)),
    ("lever", re.compile(r"jobs(\.eu)?\.lever\.co/([A-Za-z0-9_\-.]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-.%]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I)),
    ("workday", re.compile(r"([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:([a-z]{2}-[A-Z]{2})/)?([A-Za-z0-9_\-]+)", re.I)),
    ("personio", re.compile(r"([a-z0-9\-]+)\.jobs\.personio\.(de|com)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9\-]+)\.recruitee\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_\-]+)", re.I)),
]
_BAD_KEYS = {"embed", "api", "o", "l", "j", "jobs", "job", "careers", "en", "de", "v1", "v0"}


def detect_board(url: str) -> tuple[str, str, dict] | None:
    """Recognise an ATS posting URL. Returns (kind, key, params) or None."""
    if not url:
        return None
    for kind, pat in _PATTERNS:
        m = pat.search(url)
        if not m:
            continue
        if kind == "greenhouse":
            key = m.group(1)
            params = {"eu": ".eu." in url}
        elif kind == "lever":
            key = m.group(2)
            params = {"eu": bool(m.group(1))}
        elif kind == "workday":
            tenant, wd, lang, site = m.group(1), m.group(2), m.group(3), m.group(4)
            if site.lower() in ("job", "details"):
                return None
            key = f"{tenant}/{site}"
            params = {"host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site}
            if lang:
                params["lang"] = lang
            return kind, key, params
        elif kind == "personio":
            key = m.group(1)
            params = {"tld": m.group(2)}
        else:
            key = m.group(1)
            params = {}
        if key.lower() in _BAD_KEYS:
            return None
        return kind, key, params
    return None


# ---------------------------------------------------------------- source


class BoardsSource(Source):
    """Polls every enabled company board stored in the DB (seeded from config + learned automatically)."""

    name = "boards"
    label = "Company sites"

    def __init__(self, settings, params=None, db=None):
        super().__init__(settings, params)
        self.db = db
        self.last_errors: dict[str, str] = {}

    _pool: ThreadPoolExecutor | None = None  # persistent: http.session() is per thread, so a new pool per run
                                             # meant ~140 fresh TLS handshakes every run (≈40% of the bot's CPU)

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        boards = self.db.boards(enabled_only=True)
        jobs: list[RawJob] = []
        workers = int(self.params.get("workers", 8))

        def run(b: dict) -> tuple[dict, list[RawJob] | str | None, str]:
            fn = ADAPTERS.get(b["kind"])
            # slow / heavy portals (e.g. the UN API: ~5 requests per poll) are polled less often
            every = int((b.get("params") or {}).get("min_interval_sec") or 0)
            last = parse_iso(b.get("last_ok")) if b.get("last_ok") else None
            if every and last and (datetime.now(timezone.utc) - last).total_seconds() < every:
                return b, "skip", ""
            if not fn:
                return b, None, f"unknown kind {b['kind']}"
            try:
                res = fn(b["key"], b["params"], b.get("company") or "")
                for j in res:
                    j.meta.setdefault("board_kind", b["kind"])
                    j.meta.setdefault("board_key", b["key"])
                    j.meta["board_id"] = b["id"]
                    j.meta["board_new"] = not b["bootstrapped"]
                return b, res, ""
            except Exception as e:  # noqa: BLE001 - one bad board must not stop the others
                return b, None, f"{type(e).__name__}: {e}"

        if BoardsSource._pool is None:
            BoardsSource._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="boards")
        for b, res, err in BoardsSource._pool.map(run, boards):
            if res == "skip":
                continue
            if res is None:
                self.db.board_result(b["id"], False, err)
                self.last_errors[f"{b['kind']}:{b['key']}"] = err
                log.info("board %s:%s failed: %s", b["kind"], b["key"], err)
            else:
                self.db.board_result(b["id"], True)
                jobs.extend(res)
        return jobs

    def fetch_detail(self, job: RawJob) -> RawJob:
        fn = DETAILS.get(job.meta.get("board_kind", ""))
        return fn(job) if fn else job


def probe_board(kind: str, key: str, params: dict) -> int | None:
    """Return number of (filtered) jobs if the board responds, else None. Used when learning boards."""
    fn = ADAPTERS.get(kind)
    if not fn:
        return None
    try:
        return len(fn(key, params, ""))
    except Exception:  # noqa: BLE001
        return None


def board_label(kind: str) -> str:
    return KIND_LABEL.get(kind, kind)


def host_label(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.")
    except ValueError:
        return ""


# ---------------------------------------------------------------- more systems (international organisations, small ATS)
from .boards_extra import EXTRA_ADAPTERS, EXTRA_DETAILS, EXTRA_LABELS, EXTRA_PATTERNS  # noqa: E402

ADAPTERS.update(EXTRA_ADAPTERS)
DETAILS.update(EXTRA_DETAILS)
KIND_LABEL.update(EXTRA_LABELS)
_PATTERNS.extend(EXTRA_PATTERNS)
