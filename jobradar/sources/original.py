"""Full text for postings that come only as a snippet (Jooble, Careerjet).

Their own job pages are blocked for the server (Careerjet answers "Verification required" to any data-centre IP,
Jooble answers 403), so the API snippet (~250 chars) was all the AI saw → "hours / German not stated" and jobs that
are clearly Vollzeit on the page slipped through. Here we look for the ORIGINAL posting instead:
  1. the same job already seen from another source (LinkedIn, karriere.at, ...) with a full description;
  2. the company's ATS by slug guess (Greenhouse, Lever, Personio, ...);
  3. JOIN (join.com) by slug guess — many small companies/startups post there and Jooble/Careerjet copy from it.
A paid web search (Claude) is the last resort and is called from the pipeline only for jobs that would be sent.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
from datetime import timedelta

from bs4 import BeautifulSoup

from .. import http
from ..util import company_key, html_to_text, iso, norm_company, norm_title, now_utc, slug_candidates
from .sites import _jobposting

log = logging.getLogger("jobradar.original")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HDR = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}
SNIPPET_SOURCES = ("jooble", "careerjet")
MIN_FULL = 600

_BLOCK = re.compile(
    r"verification required|ungew[öo]hnlichen datenverkehr|unusual traffic|are you a robot|not a robot|"
    r"captcha|access denied|attention required|just a moment|enable javascript and cookies|request blocked", re.I)

# Signals in title/snippet that the role is compatible with studies (used when only a snippet is available)
STUDENT_SIGNAL = re.compile(
    r"praktik|pflichtprakti|ferialprakti|intern\b|internship|werkstudent|working[ -]?student|\bstudent|studierend|"
    r"studenten|teilzeit|part[- ]?time|geringf[üu]gig|nebenjob|minijob|"
    r"\b(8|10|12|15|16|20|24|25)\s*(?:[-–]|bis|to)?\s*(?:\d{1,2}\s*)?(?:h|std\.?|stunden|hours|wochenstunden)\b|"
    r"\b(?:10|15|16|20|25)\s*(?:h|std)?\s*/\s*(?:woche|week|wk)\b", re.I)


def looks_blocked(text: str) -> bool:
    """A bot-protection / verification page instead of the job ad."""
    return bool(text) and len(text) < 3000 and bool(_BLOCK.search(text))


def has_student_signal(*texts: str) -> bool:
    return any(STUDENT_SIGNAL.search(t or "") for t in texts)


def _sim(a: str, b: str) -> float:
    from ..links import title_similarity  # local import: links imports sources.boards
    return title_similarity(a, b)


def _same_company(a: str, b: str) -> bool:
    ta = {t for t in norm_company(a).split() if len(t) > 1}
    tb = {t for t in norm_company(b).split() if len(t) > 1}
    return bool(ta & tb) or company_key(a) == company_key(b)


def fetch_posting(url: str, title: str = "") -> dict | None:
    """Download a posting page and return its full text (schema.org JobPosting preferred) or None."""
    try:
        r = http.get(url, headers=HDR, ok=(200,), timeout=20)
    except Exception:  # noqa: BLE001
        return None
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = "utf-8"
    page = r.text
    jp = _jobposting(page) or {}
    desc = html_to_text(jp.get("description") or "") if jp.get("description") else ""
    if len(desc) < 300:
        soup = BeautifulSoup(page, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
            t.decompose()
        main = soup.select_one("main") or soup.body
        body = html_to_text(str(main)) if main else ""
        if len(body) > len(desc):
            desc = body
    if len(desc) < 400 or looks_blocked(desc):
        return None
    jtitle = htmllib.unescape(str(jp.get("title") or "")).strip()
    if title:
        if jtitle and _sim(jtitle, title) < 0.7:
            return None
        if not jtitle and norm_title(title)[:25] not in norm_title(desc[:4000]):
            return None
    out = {"url": r.url or url, "description": desc[:15000], "title": jtitle,
           "employment_type": jp.get("employmentType") or ""}
    if isinstance(out["employment_type"], list):
        out["employment_type"] = ", ".join(map(str, out["employment_type"]))
    if jp.get("baseSalary"):
        out["salary"] = json.dumps(jp["baseSalary"], ensure_ascii=False)[:200]
    return out


def from_db(db, company: str, title: str) -> dict | None:
    """The same posting seen from another source that had the full text."""
    if db is None:
        return None
    since = iso(now_utc() - timedelta(days=45))
    rows = db.query(
        "SELECT source, title, description, url, apply_url FROM jobs WHERE fingerprint LIKE ? AND first_seen>=? "
        "AND length(description)>=? AND source NOT IN ('jooble','careerjet') ORDER BY length(description) DESC LIMIT 30",
        (company_key(company) + "|%", since, MIN_FULL))
    for r in rows:
        r = dict(r)
        if _sim(r["title"], title) >= 0.82 and not looks_blocked(r["description"]):
            return {"url": r.get("apply_url") or r.get("url") or "", "description": r["description"],
                    "via": f"db:{r['source']}"}
    return None


def join_lookup(company: str, title: str, max_pages: int = 8) -> str | None:
    """Find the posting on JOIN (join.com/companies/<slug>) by guessing the company slug."""
    for slug in slug_candidates(company)[:3]:
        try:
            r = http.get(f"https://join.com/companies/{slug}", headers=HDR, ok=(200, 404, 410), timeout=15)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code != 200:
            continue
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            continue
        try:
            comp = json.loads(m.group(1))["props"]["pageProps"]["initialState"].get("company") or {}
        except (ValueError, KeyError, TypeError):
            continue
        cid, domain = comp.get("id"), comp.get("domain") or slug
        if not cid or not _same_company(comp.get("name") or "", company):
            continue
        for page in range(1, max_pages + 1):
            try:  # the public API accepts pageSize=5 only
                d = http.get_json(f"https://join.com/api/public/companies/{cid}/jobs?page={page}&pageSize=5",
                                  headers={"User-Agent": UA}, timeout=15)
            except Exception:  # noqa: BLE001
                break
            for it in d.get("items") or []:
                if _sim(it.get("title") or "", title) >= 0.82 and it.get("idParam"):
                    return f"https://join.com/companies/{domain}/{it['idParam']}"
            if page >= int((d.get("pagination") or {}).get("pageCount") or 0):
                break
    return None


def find_original(db, company: str, title: str) -> dict | None:
    """Free lookups for the original posting. Returns {url, description, ...} or None. Blocking (network)."""
    if not company or not title:
        return None
    hit = from_db(db, company, title)
    if hit:
        return hit
    from ..links import guess_ats_link
    try:
        found = guess_ats_link(company, title, timeout_budget=12)
    except Exception:  # noqa: BLE001
        found = None
    if found:
        p = fetch_posting(found[0], title)
        if p:
            p["via"] = f"ats:{found[1][0]}"
            return p
    url = join_lookup(company, title)
    if url:
        p = fetch_posting(url, title)
        if p:
            p["via"] = "join"
            return p
    return None
