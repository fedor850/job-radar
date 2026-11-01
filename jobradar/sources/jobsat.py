"""jobs.at — large Austrian job board that also lists many international employers' postings (often in English).

The search page has no date sort/pagination without JS, so we query many keyword pages (≈15 newest/most relevant
ads each) for Vienna; repeats are dropped by the DB.
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_relative_age
from .base import Source

LIST = "https://www.jobs.at/j/{kw}/{loc}"
DETAIL = "https://www.jobs.at/i/{id}"
HDR = {"Accept": "text/html", "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}
DEFAULT_KEYWORDS = [
    "praktikum", "intern", "internship", "werkstudent", "working-student", "student", "studentenjob", "trainee",
    "junior", "graduate", "teilzeit", "part-time", "english", "business-development", "sales", "marketing",
    "finance", "controlling", "consulting", "analyst", "assistant", "project", "operations", "polnisch", "russisch",
    "summer-internship", "sommerpraktikum", "ferialpraktikum", "summer-analyst",
]


class JobsAtSource(Source):
    name = "jobsat"
    label = "jobs.at"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        loc = self.params.get("location_slug", "wien")
        kws = self.params.get("keywords") or DEFAULT_KEYWORDS
        out: dict[str, RawJob] = {}
        for kw in kws:
            r = http.get(LIST.format(kw=quote(kw), loc=loc), headers=HDR, ok=(200, 404, 410))
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.select("a[data-c-id]"):
                jid = a.get("data-c-id")
                if not jid or jid in out:
                    continue
                item = a
                for _ in range(8):  # climb to the listing item
                    if item.parent is None or "c-search-listing-item" in " ".join(item.get("class") or []):
                        break
                    item = item.parent
                text = item.get_text(" | ", strip=True)
                company = ""
                m = re.search(r"Firma \| (.+?) \|", text)
                if m:
                    company = m.group(1)
                location = ""
                m = re.search(r"Standort \| (.+?) \|", text)
                if m:
                    location = m.group(1)
                posted = None
                d = item.select_one("[data-job-date]")
                if d and (d.get("data-job-date") or "").isdigit():
                    posted = datetime.fromtimestamp(int(d["data-job-date"]), tz=timezone.utc)
                else:
                    m = re.search(r"vor \d+ ?[TStdMin]+\w*|Heute|Gestern", text)
                    posted = parse_relative_age(m.group(0)) if m else None
                out[jid] = RawJob(
                    source="jobsat",
                    external_id=jid,
                    title=(a.get("data-c-title") or a.get_text(" ", strip=True)).strip(),
                    company=company,
                    location=location or "Wien",
                    url=DETAIL.format(id=jid),
                    posted_at=posted,
                    description=text[:600],
                    meta={"query": kw},
                    needs_detail=True,
                )
            time.sleep(random.uniform(0.8, 1.8))
        return list(out.values())

    def fetch_detail(self, job: RawJob) -> RawJob:
        r = http.get(job.url, headers=HDR, ok=(200, 410, 404))
        if r.status_code != 200:
            job.meta["drop"] = True
            job.needs_detail = False
            return job
        soup = BeautifulSoup(r.text, "lxml")
        art = soup.select_one("article.c-job-detail-text") or soup.select_one(".c-job-detail")
        if art:
            job.description = html_to_text(str(art))
        comp = soup.select_one("a.c-job-detail-title__company")
        if comp and not job.company:
            job.company = comp.get_text(" ", strip=True)
        meta = soup.select_one(".c-job-detail-meta")
        if meta:
            job.meta["facts"] = meta.get_text(" · ", strip=True)[:300]
        for a in soup.select("a[href^='http']"):
            href = a["href"]
            if "jobs.at" not in href and re.search(r"bewerb|apply|zur stellenanzeige|zum job", a.get_text(" ", strip=True), re.I):
                job.apply_url = href
                break
        job.needs_detail = False
        return job
