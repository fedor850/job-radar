"""Official job-search APIs: Jooble and Careerjet. Both need a free API key (JOOBLE_API_KEY / CAREERJET_API_KEY in
secrets.env); without a key the source stays disabled and does nothing.

Both return only a short snippet (~250 chars) and a redirect link. Their own pages are blocked for the server
(Careerjet: "Verification required", Jooble: 403), so fetch_detail() looks for the ORIGINAL posting
(see original.py). If none is found the job is marked meta["snippet_only"] and the pipeline treats it strictly.
"""
from __future__ import annotations

import base64
import hashlib
import random
import re
import time
from datetime import timezone

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_iso
from .base import Source
from .original import find_original, looks_blocked

DEFAULT_KEYWORDS = [
    "praktikum", "internship", "werkstudent", "working student", "student", "trainee", "junior", "graduate",
    "business development", "sales", "finance", "controlling", "consulting", "analyst", "marketing", "assistant",
    "project", "operations", "english", "part-time",
    "summer internship", "sommerpraktikum", "ferialpraktikum", "summer analyst",
]
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _page_text(url: str) -> str:
    try:
        r = http.get(url, headers={"Accept": "text/html", "User-Agent": UA}, ok=(200,), timeout=20)
    except Exception:  # noqa: BLE001
        return ""
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        t.decompose()
    main = soup.select_one("main") or soup.body
    text = html_to_text(str(main)) if main else ""
    return "" if looks_blocked(text) else text


class _ApiSource(Source):
    key_name = ""

    def __init__(self, settings, params=None, db=None):
        super().__init__(settings, params)
        self.db = db

    def _next_keywords(self, default_n: int) -> list[str]:
        """Round-robin over the keyword list so one run uses only a few requests."""
        kws = self.params.get("keywords") or DEFAULT_KEYWORDS
        n = int(self.params.get("keywords_per_run", default_n))
        pos = int(self.db.kv_get(f"api_rr:{self.name}", 0) or 0) if self.db else 0
        picked = [kws[(pos + i) % len(kws)] for i in range(min(n, len(kws)))]
        if self.db:
            self.db.kv_set(f"api_rr:{self.name}", (pos + n) % len(kws))
        return picked

    def _budget_ok(self, cost: int) -> bool:
        cap = int(self.params.get("request_cap", 0))
        if not cap or not self.db:
            return True
        used = int(self.db.kv_get(f"api_used:{self.name}", 0) or 0)
        if used + cost > cap:
            self.log.warning("%s request cap reached (%s/%s)", self.name, used, cap)
            return False
        self.db.kv_set(f"api_used:{self.name}", used + cost)
        return True

    @property
    def key(self) -> str:
        return self.settings.secret(self.key_name)

    @property
    def enabled(self) -> bool:
        return bool(self.params.get("enabled", True)) and bool(self.key)

    def fetch_detail(self, job: RawJob) -> RawJob:
        job.needs_detail = False
        text = _page_text(job.url) if self.params.get("try_own_page", True) else ""
        if len(text) > max(600, len(job.description) + 200):
            job.description = text[:15000]
            job.meta["full_text_from"] = "page"
            return job
        try:
            orig = find_original(self.db, job.company, job.title)
        except Exception as e:  # noqa: BLE001
            self.log.info("original lookup failed for %s: %s", job.title, e)
            orig = None
        if orig and len(orig.get("description") or "") > len(job.description):
            job.meta["snippet"] = job.description[:600]
            job.description = orig["description"][:15000]
            job.meta["full_text_from"] = orig.get("via") or orig.get("url") or "original"
            for k in ("employment_type", "salary"):
                if orig.get(k) and not job.meta.get(k):
                    job.meta[k] = orig[k]
            if orig.get("url") and not job.apply_url:
                job.apply_url = orig["url"]
            self.log.info("%s: full text for '%s' via %s", self.name, job.title, job.meta["full_text_from"])
        else:
            job.meta["snippet_only"] = True
        return job


class JoobleSource(_ApiSource):
    name = "jooble"
    label = "Jooble"
    key_name = "JOOBLE_API_KEY"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        if not self.key:
            return []
        out: dict[str, RawJob] = {}
        self.begin()
        for kw in self._next_keywords(2):
            if not self._budget_ok(1):
                break
            try:
                data = http.post_json(f"https://{self.params.get('host', 'at.jooble.org')}/api/{self.key}",
                                      {"keywords": kw, "location": self.params.get("location", "Vienna, Austria"),
                                       "radius": "25", "page": "1"}, timeout=30, retry_block=(403,))
            except Exception as e:  # noqa: BLE001 - the second keyword may still work
                self.note_error(e, f"keyword {kw}")
                continue
            for j in data.get("jobs") or []:
                jid = str(j.get("id") or j.get("link") or "")
                if not jid or jid in out:
                    continue
                posted = parse_iso(str(j.get("updated") or ""))
                if posted and posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
                out[jid] = RawJob(
                    source=self.name, external_id=jid, title=re.sub(r"</?b>", "", j.get("title") or "").strip(),
                    company=(j.get("company") or "").strip(), location=j.get("location") or "Wien",
                    url=j.get("link") or "", description=html_to_text(j.get("snippet") or ""), posted_at=posted,
                    meta={"employment_type": j.get("type") or "", "salary": j.get("salary") or "",
                          "origin": j.get("source") or ""},
                    needs_detail=True)
            time.sleep(random.uniform(0.3, 0.8))
        return self.finish(list(out.values()))


class CareerjetSource(_ApiSource):
    name = "careerjet"
    label = "Careerjet"
    key_name = "CAREERJET_API_KEY"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        if not self.key:
            return []
        auth = base64.b64encode(f"{self.key}:".encode()).decode()
        hdr = {"Authorization": f"Basic {auth}", "Accept": "application/json",
               "Referer": self.params.get("referer", "")}  # Careerjet requires the referer registered for the key
        out: dict[str, RawJob] = {}
        self.begin()
        for kw in self._next_keywords(4):
            for locale in self.params.get("locales") or ["de_AT", "en_GB"]:
                if not self._budget_ok(1):
                    break
                try:
                    r = http.get("https://search.api.careerjet.net/v4/query", headers=hdr, timeout=30, params={
                        "locale_code": locale, "keywords": kw, "location": self.params.get("location", "Wien"),
                        "sort": "date", "page_size": 50, "page": 1, "fragment_size": 400,
                        "user_ip": self.params.get("user_ip", ""), "user_agent": UA})  # your server's public IP
                    jobs = r.json().get("jobs") or []
                except Exception as e:  # noqa: BLE001 - one bad keyword / locale must not lose the rest
                    self.note_error(e, f"{kw}/{locale}")
                    continue
                for j in jobs:
                    url = j.get("url") or ""
                    # jobviewtrack links change on every request → a stable id from title+company+location
                    jid = str(j.get("id") or "") or hashlib.sha1(
                        f"{j.get('title')}|{j.get('company')}".lower().encode()).hexdigest()[:20]
                    if not jid or jid in out:
                        continue
                    posted = parse_iso(str(j.get("date") or ""))
                    if posted is None and j.get("date"):
                        try:
                            from email.utils import parsedate_to_datetime
                            posted = parsedate_to_datetime(j["date"])
                        except Exception:  # noqa: BLE001
                            posted = None
                    if posted and posted.tzinfo is None:
                        posted = posted.replace(tzinfo=timezone.utc)
                    out[jid] = RawJob(
                        source=self.name, external_id=jid, title=(j.get("title") or "").strip(),
                        company=(j.get("company") or "").strip(), location=j.get("locations") or "Wien", url=url,
                        description=html_to_text(j.get("description") or ""), posted_at=posted,
                        meta={"salary": j.get("salary") or "", "origin": j.get("site") or ""}, needs_detail=True)
                time.sleep(random.uniform(0.3, 0.8))
        return self.finish(list(out.values()))
