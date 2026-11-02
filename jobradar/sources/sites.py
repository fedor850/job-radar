"""Generic job sites configured in config.yaml (sources.sites): a few list pages → links → detail pages.

Detail pages are parsed from schema.org JobPosting (ld+json) when present, otherwise from <h1> + main text.
Details are fetched inside fetch() only for links the DB has not seen yet, so title and company are known
before deduplication (no false "duplicate" matches on generic titles).

Config example:
  - name: adzuna                       # source name (no ":"), shown in /status
    label: Adzuna
    interval_sec: 900
    urls: ["https://www.adzuna.at/search?w=Wien&sb=date&p={page}"]
    pages: 3                           # {page} = 1..pages (optional)
    keywords: [praktikum, intern]      # {kw} in urls (optional; one request per keyword)
    link: 'adzuna\\.at/details/(?P<id>\\d+)'
    detail: "https://www.adzuna.at/details/{id}"   # may use {id}, {slug}, {url}
    vienna_only: true                  # drop details whose location is clearly outside Vienna
"""
from __future__ import annotations

import html as htmllib
import json
import random
import re
import time
from datetime import timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import VIENNA_RE, html_to_text, location_ok, parse_iso
from .base import Source

HDR = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}
_GENERIC_ANCHOR = re.compile(r"^(mehr|more|details?|weitere informationen|jetzt bewerben|apply|ansehen|view)\b", re.I)


def _text(r) -> str:
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = "utf-8"
    return r.text


def _jobposting(html: str) -> dict | None:
    for m in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            d = json.loads(m.strip())
        except ValueError:
            continue
        items = d if isinstance(d, list) else (d.get("@graph") or [d])
        for x in items:
            if isinstance(x, dict) and (x.get("@type") == "JobPosting" or "JobPosting" in (x.get("@type") or [])):
                return x
    return None


def _location(jp: dict) -> str:
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, str):
            parts.append(a)
            continue
        s = " ".join(str(a.get(k) or "") for k in ("postalCode", "addressLocality", "addressRegion", "addressCountry")
                     if isinstance(a.get(k), (str, int)))
        parts.append(s.strip())
    if (jp.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        parts.append("Remote")
    return " / ".join(p for p in parts if p)


def _org(jp: dict) -> str:
    o = jp.get("hiringOrganization") or {}
    return (o.get("name") if isinstance(o, dict) else str(o)) or ""


def _slug_title(slug: str) -> str:
    s = re.sub(r"[-_]+", " ", slug or "").strip()
    return s[:1].upper() + s[1:]


class SiteSource(Source):
    """One configured site; instantiated once per entry of sources.sites."""

    def __init__(self, settings, params=None, db=None):
        super().__init__(settings, params)
        self.name = params["name"]
        self.label = params.get("label", self.name)
        self.db = db
        self.link_re = re.compile(params["link"], re.I)

    def _list_urls(self, backfill: bool) -> list[str]:
        out = []
        pages = int(self.params.get("backfill_pages" if backfill else "pages", self.params.get("pages", 1)))
        for tpl in self.params.get("urls") or []:
            kws = self.params.get("keywords") if "{kw}" in tpl else [""]
            for kw in kws or [""]:
                for p in range(1, (pages if "{page}" in tpl else 1) + 1):
                    out.append(tpl.replace("{kw}", str(kw)).replace("{page}", str(p)))
        return out

    def _links(self, html: str, base: str) -> dict[str, dict]:
        """id -> {url, slug, title_hint}"""
        found: dict[str, dict] = {}
        raw = html.replace("\\u002F", "/").replace("\\/", "/")
        for m in self.link_re.finditer(raw):
            g = m.groupdict()
            jid = g.get("id") or m.group(1)
            if not jid or jid in found:
                continue
            url = g.get("url") or ""
            if url:
                url = urljoin(base, htmllib.unescape(url))
            tpl = self.params.get("detail")
            if tpl:
                url = tpl.format(id=jid, slug=g.get("slug") or "", url=url)
            found[jid] = {"url": url, "slug": g.get("slug") or "", "title": ""}
        # anchor texts as title hints
        try:
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select("a[href]"):
                m = self.link_re.search(a["href"]) or self.link_re.search(f'href="{a["href"]}"')
                if not m:
                    continue
                jid = m.groupdict().get("id") or m.group(1)
                t = a.get_text(" ", strip=True)
                if jid in found and t and not _GENERIC_ANCHOR.match(t) and len(t) > len(found[jid]["title"]):
                    found[jid]["title"] = t[:200]
        except Exception:  # noqa: BLE001
            pass
        return found

    def _detail(self, jid: str, info: dict) -> RawJob | None:
        r = http.get(info["url"], headers=HDR, ok=(200, 404, 410))
        if r.status_code != 200:
            return None
        html = _text(r)
        jp = _jobposting(html) or {}
        soup = BeautifulSoup(html, "lxml")
        title = (jp.get("title") or "").strip()
        if not title:
            h1 = soup.select_one("h1")
            title = h1.get_text(" ", strip=True) if h1 else ""
        title = htmllib.unescape(title or info.get("title") or _slug_title(info.get("slug", "")))
        desc = html_to_text(jp.get("description") or "") if jp.get("description") else ""
        if len(desc) < 300:
            for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                t.decompose()
            main = soup.select_one(self.params.get("main_selector") or "main") or soup.body
            body = html_to_text(str(main)) if main else ""
            if len(body) > len(desc):
                desc = body
        location = _location(jp) if jp else ""
        if self.params.get("vienna_only", True) and location and not location_ok(location, desc):
            if not VIENNA_RE.search(f"{title} {desc[:1500]}"):
                return None
        company = self.params.get("company") or htmllib.unescape(_org(jp))
        if not company and self.params.get("company_regex"):
            m = re.search(self.params["company_regex"], desc[:600], re.M)
            if m:
                company = m.group(1).strip()[:120]
        posted = parse_iso(str(jp.get("datePosted") or "")) if jp.get("datePosted") else None
        if posted and posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        meta = {"employment_type": jp.get("employmentType") or "", "valid_through": str(jp.get("validThrough") or "")}
        sal = jp.get("baseSalary")
        if sal:
            meta["salary"] = json.dumps(sal, ensure_ascii=False)[:200]
        apply_url = ""
        if self.params.get("apply_link"):
            m = re.search(self.params["apply_link"], html)
            if m:
                apply_url = urljoin(info["url"], htmllib.unescape(m.group(1)))
        return RawJob(
            source=self.name, external_id=str(jid), title=title,
            company=company,
            location=location or self.params.get("default_location", "Wien"), url=info["url"],
            apply_url=apply_url, description=desc[:15000], posted_at=posted, meta=meta, needs_detail=False,
        )

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        self.begin()
        links: dict[str, dict] = {}
        pages_ok = 0
        for url in self._list_urls(backfill):
            try:
                # 202 / 403 from an anti-bot layer are random → one retry with a fresh session
                r = http.get(url, headers=HDR, ok=(200, 404), retry_block=(202, 403))
            except Exception as e:  # noqa: BLE001 - one bad list page must not lose the other pages
                self.note_error(e, f"list page {url}")
                time.sleep(random.uniform(0.6, 1.5))
                continue
            if r.status_code == 200:
                pages_ok += 1
                for k, v in self._links(_text(r), url).items():
                    links.setdefault(k, v)
            else:
                pages_ok += 1  # 404 = the site answered
            time.sleep(random.uniform(0.6, 1.5))
        if not pages_ok and self.errors:
            raise self.errors[0]
        # this source returns only links it has not seen yet, so "0 jobs" usually means "nothing new";
        # the zero-yield health check looks at how many job links the list pages showed instead
        self.last_list_count = len(links)
        cap = int(self.params.get("max_details", 40))
        out: list[RawJob] = []
        transient = 0
        for jid, info in links.items():
            if self.db is not None and self.db.job_exists(f"{self.name}:{jid}"):
                continue
            if len(out) >= cap:
                break
            try:
                job = self._detail(jid, info)
            except Exception as e:  # noqa: BLE001
                # a timeout / 429 / 5xx says nothing about the ad itself: do NOT remember it as "dropped"
                # (that would lose it for good) — it is simply fetched again on the next run
                self.note_error(e, f"detail {info.get('url')}")
                transient += 1
                if transient >= 5:  # the site is struggling: stop hammering it, resume next run
                    break
                time.sleep(random.uniform(1.0, 2.5))
                continue
            if job is None:
                # remember irrelevant/removed links so we do not fetch them again
                job = RawJob(source=self.name, external_id=str(jid), title=info.get("title") or "-", company="",
                             location="", url=info["url"], meta={"drop": True})
            out.append(job)
            time.sleep(random.uniform(0.4, 1.0))
        return out
