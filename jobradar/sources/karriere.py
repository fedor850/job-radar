"""karriere.at — Austria's largest job board. The site returns its page state as JSON when asked for it."""
from __future__ import annotations

import random
import re
import time

from .. import http
from ..models import RawJob
from ..util import clean_url, html_to_text, parse_relative_age
from .base import Source

LIST_URL = "https://www.karriere.at/jobs/{loc}?page={page}"
DETAIL_URL = "https://www.karriere.at/jobs/{id}"
HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}


class KarriereSource(Source):
    name = "karriere"
    label = "karriere.at"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        pages = int(self.params.get("backfill_pages" if backfill else "pages", 15 if backfill else 3))
        loc = self.params.get("location_slug", "wien")
        out: dict[str, RawJob] = {}
        self.begin()
        for page in range(1, pages + 1):
            try:
                data = http.get_json(LIST_URL.format(loc=loc, page=page), headers=HEADERS,
                                     retry_block=(403,)).get("data", {})
            except Exception as e:  # noqa: BLE001 - page 1 is already in `out`: keep it
                self.note_error(e, f"page {page}")
                break
            items = (data.get("jobsSearchList", {}).get("activeItems") or {}).get("items") or []
            if not items:
                break
            for it in items:
                j = it.get("jobsItem") if isinstance(it, dict) else None
                if not j or not j.get("id"):
                    continue
                jid = str(j["id"])
                company = (j.get("company") or {}).get("name", "")
                locs = ", ".join(l.get("name", "") for l in (j.get("locations") or []) if isinstance(l, dict))
                out[jid] = RawJob(
                    source="karriere",
                    external_id=jid,
                    title=j.get("title", ""),
                    company=company,
                    location=locs,
                    url=j.get("link") or DETAIL_URL.format(id=jid),
                    description=(j.get("summary") or "") + "\n" + (j.get("snippet") or ""),
                    posted_at=parse_relative_age(j.get("date", "")),
                    meta={
                        "employment_types": j.get("employmentTypes", ""),
                        "salary": j.get("salary", ""),
                        "homeoffice": bool(j.get("isHomeOffice")),
                        "sponsored": bool(j.get("pushedJob")),
                    },
                    needs_detail=True,
                )
            time.sleep(random.uniform(0.5, 1.2))
        return self.finish(list(out.values()))

    def fetch_detail(self, job: RawJob) -> RawJob:
        d = http.get_json(DETAIL_URL.format(id=job.external_id), headers=HEADERS).get("data", {})
        content = d.get("jobDetailContent") or {}
        text_html = (content.get("jobContent") or {}).get("text") or ""
        if text_html:
            job.description = html_to_text(text_html)
        apply_url = ((content.get("applyAction") or {}).get("applyButton") or {}).get("applyUrl") or ""
        if apply_url and "karriere.at" not in apply_url:
            job.apply_url = clean_url(apply_url)
        job.needs_detail = False
        return job


_ID = re.compile(r"/jobs/(\d+)")
