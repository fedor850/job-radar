"""More career systems: international organisations and smaller ATS (added 30.10.2026).

  unjobs     — UN Secretariat portal careers.un.org (UNOV, UNODC, UNOOSA, UNCITRAL, UNEP … in Vienna), public JSON API
  taleo      — Oracle Taleo career sections (IAEA): REST job board + job detail page
  lanteria   — Lanteria HR career sites (ICMPD): postings embedded as JSON in the page
  htmllist   — any plain HTML list page (OSCE, …): CSS selectors from config, detail page fetched later
  teamtailor — <company>.teamtailor.com RSS
  bamboohr   — <company>.bamboohr.com careers JSON
  breezy     — <company>.breezy.hr JSON
  pinpoint   — <company>.pinpointhq.com postings JSON
  join       — join.com company pages (public API)

Every adapter returns RawJobs already filtered to Vienna / remote-from-Austria (boards._keep), like the others.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_iso
from .boards import _keep

HDR = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}


def _txt(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


# ---------------------------------------------------------------- UN Secretariat (Inspira / careers.un.org)

UN_API = "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"


def unjobs(key: str, params: dict, company: str) -> list[RawJob]:
    """The API ignores the duty-station filter → page through all openings (≈ 450, 100 per page) and keep
    those whose duty station matches params.duty_station (default VIENNA)."""
    station = re.compile(params.get("duty_station", r"\bVIENNA\b"), re.I)
    out: list[RawJob] = []
    for page in range(int(params.get("pages", 8))):
        payload = {"filterConfig": {"keyword": ""},
                   "pagination": {"page": page, "itemPerPage": 100, "sortBy": "startDate", "sortDirection": -1}}
        d = (http.post_json(UN_API, payload, timeout=40) or {}).get("data") or {}
        items = d.get("list") or []
        for j in items:
            ds = j.get("dutyStation") or []
            names = " / ".join(_txt(x.get("description")) for x in ds if isinstance(x, dict))
            if not station.search(names):
                continue
            jid = str(j.get("jobId") or "")
            if not jid:
                continue
            dept = (j.get("dept") or {}).get("name", "") if isinstance(j.get("dept"), dict) else ""
            level = (j.get("jl") or {}).get("name", "") if isinstance(j.get("jl"), dict) else _txt(j.get("jobLevel"))
            cat = (j.get("jc") or {}).get("name", "") if isinstance(j.get("jc"), dict) else _txt(j.get("categoryCode"))
            deadline = _txt(j.get("endDate"))[:10]
            url = f"https://careers.un.org/jobSearchDescription/{jid}?language=en"
            head = f"Organisation: {dept or 'United Nations'}\nCategory: {cat}\nLevel: {level}\nDuty station: {names}\n" \
                   f"Deadline: {deadline}\n\n"
            out.append(RawJob(
                source=f"unjobs:{key}", external_id=jid,
                title=_txt(j.get("postingTitle") or j.get("jobTitle")),
                company=f"United Nations ({dept})" if dept else (company or "United Nations"),
                location=f"{names.title()}, Austria", url=url, apply_url=url,
                description=head + html_to_text(j.get("jobDescription") or ""),
                posted_at=parse_iso(_txt(j.get("startDate"))),
                meta={"level": level, "deadline": deadline, "category": cat},
            ))
        total = int(d.get("count") or 0)
        if len(items) < 100 or (page + 1) * 100 >= total:
            break
    return out


# ---------------------------------------------------------------- Oracle Taleo (IAEA)

def taleo(key: str, params: dict, company: str) -> list[RawJob]:
    host, portal = params["host"], params["portal"]
    section = params.get("section", "ex")
    out: list[RawJob] = []
    for page in range(1, int(params.get("pages", 10)) + 1):
        payload = {
            "multilineEnabled": False,
            "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
            "fieldData": {"fields": {"KEYWORD": "", "LOCATION": "", "ORGANIZATION": ""}, "valid": True},
            "filterSelectionParam": {"searchFilterSelections": []},
            "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
            "pageNo": page,
        }
        d = http.post_json(f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}",
                           payload, headers={"tz": "GMT+01:00"}, timeout=40) or {}
        reqs = d.get("requisitionList") or []
        for r in reqs:
            col = r.get("column") or []
            title = _txt(col[0]) if col else ""
            loc = col[1] if len(col) > 1 else ""
            try:
                loc = " / ".join(json.loads(loc)) if str(loc).startswith("[") else _txt(loc)
            except ValueError:
                loc = _txt(loc)
            if not title or not _keep(loc, "", False, params):
                continue
            jid = str(r.get("jobId") or "")
            url = f"https://{host}/careersection/{section}/jobdetail.ftl?job={jid}&lang=en"
            out.append(RawJob(
                source=f"taleo:{key}", external_id=jid, title=title, company=company or key, location=loc,
                url=url, apply_url=url, needs_detail=True,
                meta={"board_kind": "taleo", "deadline": _txt(col[2]) if len(col) > 2 else "",
                      "contest": r.get("contestNo") or ""},
            ))
        pd = d.get("pagingData") or {}
        size, total = int(pd.get("pageSize") or 25), int(pd.get("totalCount") or 0)
        if not reqs or page * size >= total:
            break
    return out


_TALEO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}), \d")


def taleo_detail(job: RawJob) -> RawJob:
    r = http.get(job.url, headers=HDR, timeout=30)
    m = re.search(r'id="initialHistory"[^>]*value="([^"]*)"', r.text)
    if not m:
        job.description = html_to_text(r.text, 8000)
        job.needs_detail = False
        return job
    raw = urllib.parse.unquote(htmllib.unescape(m.group(1)))
    parts, seen = [], set()
    posted = None
    for tok in re.split(r"!\|!|!\$!", raw):
        tok = tok.replace("\\:", ":").replace("!*!", "\n").strip()
        if not posted and _TALEO_DATE.match(tok):
            posted = datetime.strptime(_TALEO_DATE.match(tok).group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        text = html_to_text(tok) if "<" in tok else tok
        if len(text) >= 150 and text not in seen:  # Taleo repeats every field twice
            seen.add(text)
            parts.append(text)
    job.description = "\n\n".join(parts)[:15000] or html_to_text(r.text, 8000)
    if posted:
        job.posted_at = posted
    dl = job.meta.get("deadline")
    if dl:
        job.description = f"Closing date: {dl}\nLocation: {job.location}\n\n" + job.description
    job.needs_detail = False
    return job


# ---------------------------------------------------------------- Lanteria HR (ICMPD)

def lanteria(key: str, params: dict, company: str) -> list[RawJob]:
    host = params["host"]
    t = http.get(f"https://{host}/", headers=HDR, timeout=30).text
    i = t.find("jobOpenings")
    j = t.find("[", i) if i >= 0 else -1
    if j < 0:
        raise RuntimeError("lanteria: jobOpenings not found (page changed?)")
    arr, _ = json.JSONDecoder().raw_decode(t[j:])
    out = []
    for x in arr:
        loc = ", ".join(x.get("locations") or [])
        if not _keep(loc, "", False, params):
            continue
        url = f"https://{host}/Home/JobOpeningDetails?id={x.get('id')}"
        out.append(RawJob(source=f"lanteria:{key}", external_id=str(x.get("id")), title=_txt(x.get("title")),
                          company=company or key, location=loc, url=url, apply_url=url,
                          description=html_to_text(x.get("description") or ""), posted_at=parse_iso(x.get("startDate"))))
    return out


# ---------------------------------------------------------------- generic HTML list (OSCE, …)

def htmllist(key: str, params: dict, company: str) -> list[RawJob]:
    """params: url | urls, row (CSS), link (CSS in row, default 'a[href]'), location (CSS in row, optional),
    id_regex (on the link, default: last number), location_default."""
    urls = params.get("urls") or [params["url"]]
    out: dict[str, RawJob] = {}
    for u in urls:
        soup = BeautifulSoup(http.get(u, headers=HDR, timeout=30).text, "lxml")
        rows = soup.select(params["row"])
        if not rows and params.get("require_rows", True):
            raise RuntimeError(f"htmllist: no rows for selector {params['row']!r} (page changed?)")
        for row in rows:
            a = row.select_one(params.get("link", "a[href]"))
            if not a or not a.get("href"):
                continue
            href = urljoin(u, a["href"])
            m = re.search(params.get("id_regex", r"(\d{3,})(?!.*\d{3,})"), href)
            jid = m.group(1) if m else hashlib.md5(href.encode()).hexdigest()[:12]
            loc_el = row.select_one(params["location"]) if params.get("location") else None
            loc = _txt(loc_el.get_text(" ")) if loc_el else params.get("location_default", "")
            title = _txt(a.get_text(" "))
            if not title or not _keep(loc, title, False, params):
                continue
            out.setdefault(jid, RawJob(source=f"htmllist:{key}", external_id=jid, title=title, company=company or key,
                                       location=loc, url=href, apply_url=href, needs_detail=True,
                                       meta={"board_kind": "htmllist", "detail_selector": params.get("detail", "")}))
    return list(out.values())


def _jsonld_posting(html: str) -> dict | None:
    for m in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(m.strip())
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else (d.get("@graph") or [d])):
            if isinstance(x, dict) and "JobPosting" in str(x.get("@type")):
                return x
    return None


def page_detail(job: RawJob) -> RawJob:
    """Detail for htmllist / breezy / join: schema.org JobPosting, else the configured CSS block, else <main>."""
    r = http.get(job.url, headers=HDR, timeout=30)
    jp = _jsonld_posting(r.text) or {}
    desc = html_to_text(jp.get("description") or "") if jp.get("description") else ""
    if len(desc) < 300:
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        sel = job.meta.get("detail_selector") or ""
        el = (soup.select_one(sel) if sel else None) or soup.select_one("main") or soup.body
        body = html_to_text(str(el)) if el else ""
        if len(body) > len(desc):
            desc = body
    job.description = desc[:15000]
    if jp.get("datePosted") and not job.posted_at:
        job.posted_at = parse_iso(str(jp["datePosted"]))
    if jp.get("employmentType"):
        job.meta["employment_type"] = str(jp["employmentType"])
    job.needs_detail = False
    return job


# ---------------------------------------------------------------- Teamtailor (RSS)

def teamtailor(key: str, params: dict, company: str) -> list[RawJob]:
    r = http.get(f"https://{key}.teamtailor.com/jobs.rss", headers={"Accept": "application/rss+xml"}, timeout=30)
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        def g(tag):
            el = it.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        locs = []
        for el in it.iter():
            tag = el.tag.split("}")[-1].lower()
            if tag in ("city", "location", "name", "country", "remotestatus") and (el.text or "").strip():
                locs.append(el.text.strip())
        loc = " / ".join(dict.fromkeys(locs))
        desc = html_to_text(g("description"))
        remote = bool(re.search(r"remote|fully", loc, re.I))
        if not _keep(loc, desc if not loc else "", remote, params):
            continue
        link = g("link")
        jid = re.search(r"/jobs/(\d+)", link)
        out.append(RawJob(source=f"teamtailor:{key}", external_id=jid.group(1) if jid else link, title=g("title"),
                          company=company or key, location=loc, url=link, apply_url=link, description=desc,
                          posted_at=_rfc822(g("pubDate")) if g("pubDate") else None))
    return out


def _rfc822(s: str) -> datetime | None:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- BambooHR

def bamboohr(key: str, params: dict, company: str) -> list[RawJob]:
    d = http.get_json(f"https://{key}.bamboohr.com/careers/list", timeout=30) or {}
    out = []
    for j in d.get("result") or []:
        l = j.get("location") or {}
        loc = ", ".join(x for x in [l.get("city"), l.get("state"), l.get("addressCountry")] if x)
        if not _keep(loc, "", bool(j.get("isRemote")), params):
            continue
        url = f"https://{key}.bamboohr.com/careers/{j.get('id')}"
        out.append(RawJob(source=f"bamboohr:{key}", external_id=str(j.get("id")), title=_txt(j.get("jobOpeningName")),
                          company=company or key, location=loc, url=url, apply_url=url, needs_detail=True,
                          meta={"board_kind": "bamboohr", "bamboo_key": key,
                                "employment_type": j.get("employmentStatusLabel") or ""}))
    return out


def bamboohr_detail(job: RawJob) -> RawJob:
    key = job.meta.get("bamboo_key") or job.source.split(":", 1)[1]
    d = http.get_json(f"https://{key}.bamboohr.com/careers/{job.external_id}/detail", timeout=30) or {}
    jo = (d.get("result") or {}).get("jobOpening") or {}
    job.description = html_to_text(jo.get("description") or "")
    job.posted_at = parse_iso(jo.get("datePosted") or "") or job.posted_at
    job.needs_detail = False
    return job


# ---------------------------------------------------------------- Breezy HR

def breezy(key: str, params: dict, company: str) -> list[RawJob]:
    data = http.get_json(f"https://{key}.breezy.hr/json", timeout=30) or []
    out = []
    for j in data if isinstance(data, list) else []:
        l = j.get("location") or {}
        country = (l.get("country") or {}).get("name", "") if isinstance(l.get("country"), dict) else ""
        loc = " / ".join(x for x in [l.get("name"), l.get("city"), country] if x)
        if not _keep(loc, "", bool(l.get("is_remote")), params):
            continue
        out.append(RawJob(source=f"breezy:{key}", external_id=str(j.get("id") or j.get("friendly_id")),
                          title=_txt(j.get("name")), company=company or j.get("company", {}).get("name", key),
                          location=loc, url=j.get("url", ""), apply_url=j.get("url", ""), needs_detail=True,
                          posted_at=parse_iso(j.get("published_date") or ""),
                          meta={"board_kind": "breezy", "employment_type": (j.get("type") or {}).get("name", "")}))
    return out


# ---------------------------------------------------------------- Pinpoint

def pinpoint(key: str, params: dict, company: str) -> list[RawJob]:
    d = http.get_json(f"https://{key}.pinpointhq.com/postings.json", timeout=30) or {}
    out = []
    for j in d.get("data") or []:
        l = j.get("location") or {}
        loc = " / ".join(x for x in [l.get("name"), l.get("city"), l.get("province")] if x)
        remote = "remote" in _txt(j.get("workplace_type")).lower()
        if not _keep(loc, "", remote, params):
            continue
        desc = "\n\n".join(html_to_text(j.get(k) or "") for k in ("description", "key_responsibilities",
                                                                   "skills_knowledge_expertise", "benefits"))
        out.append(RawJob(source=f"pinpoint:{key}", external_id=str(j.get("id")), title=_txt(j.get("title")),
                          company=company or key, location=loc, url=j.get("url", ""), apply_url=j.get("url", ""),
                          description=desc.strip(), meta={"employment_type": j.get("employment_type_text") or ""}))
    return out


# ---------------------------------------------------------------- JOIN

def join(key: str, params: dict, company: str) -> list[RawJob]:
    cid = params.get("company_id")
    domain = params.get("domain") or key
    if not cid:
        r = http.get(f"https://join.com/companies/{key}", headers=HDR, timeout=30)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        comp = json.loads(m.group(1))["props"]["pageProps"]["initialState"].get("company") or {} if m else {}
        cid, domain = comp.get("id"), comp.get("domain") or key
        if not cid:
            raise RuntimeError("join: company id not found")
    out = []
    for page in range(1, 9):  # the public API serves pageSize=5 only
        d = http.get_json(f"https://join.com/api/public/companies/{cid}/jobs?page={page}&pageSize=5", timeout=30) or {}
        for it in d.get("items") or []:
            city = it.get("city") or {}
            loc = " / ".join(x for x in [city.get("cityName") if isinstance(city, dict) else str(city or ""),
                                         (it.get("country") or {}).get("name") if isinstance(it.get("country"), dict) else ""]
                             if x)
            remote = "remote" in json.dumps(it.get("workplaceType") or it.get("remoteType") or "").lower()
            if not _keep(loc, "", remote, params):
                continue
            url = f"https://join.com/companies/{domain}/{it.get('idParam')}"
            out.append(RawJob(source=f"join:{key}", external_id=str(it.get("id") or it.get("idParam")),
                              title=_txt(it.get("title")), company=company or key, location=loc, url=url, apply_url=url,
                              posted_at=parse_iso(it.get("createdAt") or ""), needs_detail=True,
                              meta={"board_kind": "join", "employment_type": json.dumps(it.get("employmentType") or "")[:60]}))
        if page >= int((d.get("pagination") or {}).get("pageCount") or 0):
            break
    return out


# ---------------------------------------------------------------- OSCE (vacancies.osce.org: session-based search)

def osce(key: str, params: dict, company: str) -> list[RawJob]:
    """GET /jobs/search/ → search id; /ajax/jobs/<id>/add/location/<loc> → new search id with the location filter;
    /jobs/search/<id>[/pageN] → 10 rows per page. Locations 61 = Secretariat Vienna, 63 = RFoM Vienna."""
    base = "https://vacancies.osce.org"
    out: dict[str, RawJob] = {}
    for loc_id in params.get("locations", [61, 63]):
        r = http.get(f"{base}/jobs/search/", headers=HDR, timeout=30)
        m = re.search(r"/jobs/search/(\d+)", r.url)
        if not m:
            raise RuntimeError("osce: no search id (site changed?)")
        res = http.get_json(f"{base}/ajax/jobs/{m.group(1)}/add/location/{loc_id}", timeout=30) or {}
        sid = str(res.get("Result") or "")
        if not sid.isdigit():
            raise RuntimeError(f"osce: location filter failed: {str(res)[:100]}")
        for page in range(1, 8):
            url = f"{base}/jobs/search/{sid}" + (f"/page{page}" if page > 1 else "")
            soup = BeautifulSoup(http.get(url, headers=HDR, timeout=30).text, "lxml")
            rows = soup.select("div.job_list_row")
            for row in rows:
                a = row.select_one("a.job_link")
                if not a or not a.get("href"):
                    continue
                href = urljoin(base, a["href"])
                mm = re.search(r"-(\d+)$", href.rstrip("/"))
                jid = mm.group(1) if mm else hashlib.md5(href.encode()).hexdigest()[:12]
                le = row.select_one("span.location")
                out.setdefault(jid, RawJob(source=f"osce:{key}", external_id=jid, title=_txt(a.get_text(" ")),
                                           company=company or "OSCE", location=_txt(le.get_text(" ")) if le else "Vienna",
                                           url=href, apply_url=href, needs_detail=True,
                                           meta={"board_kind": "osce", "detail_selector": "#job_details, .job_description, main"}))
            if len(rows) < 10:
                break
    return list(out.values())


EXTRA_ADAPTERS = {
    "unjobs": unjobs, "taleo": taleo, "lanteria": lanteria, "htmllist": htmllist, "teamtailor": teamtailor,
    "bamboohr": bamboohr, "breezy": breezy, "pinpoint": pinpoint, "join": join, "osce": osce,
}
EXTRA_DETAILS = {"taleo": taleo_detail, "htmllist": page_detail, "bamboohr": bamboohr_detail, "breezy": page_detail,
                 "join": page_detail, "osce": page_detail}
EXTRA_LABELS = {"unjobs": "UN Careers", "taleo": "Taleo", "lanteria": "Lanteria", "htmllist": "сайт",
                "teamtailor": "Teamtailor", "bamboohr": "BambooHR", "breezy": "Breezy", "pinpoint": "Pinpoint",
                "join": "JOIN", "osce": "OSCE"}
EXTRA_PATTERNS = [
    ("teamtailor", re.compile(r"([a-z0-9\-]+)\.teamtailor\.com", re.I)),
    ("bamboohr", re.compile(r"([a-z0-9\-]+)\.bamboohr\.com/(?:careers|jobs)", re.I)),
    ("breezy", re.compile(r"([a-z0-9\-]+)\.breezy\.hr", re.I)),
    ("pinpoint", re.compile(r"([a-z0-9\-]+)\.pinpointhq\.com", re.I)),
    ("join", re.compile(r"join\.com/companies/([a-z0-9\-]+)", re.I)),
]
