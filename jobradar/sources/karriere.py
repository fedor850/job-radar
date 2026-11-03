"""karriere.at — Austria's largest job board. The site returns its page state as JSON when asked for it."""
from __future__ import annotations

import random
import re
import time
from urllib.parse import quote

from .. import http
from ..models import RawJob
from ..util import clean_url, html_to_text, parse_relative_age
from .base import Source

LIST_URL = "https://www.karriere.at/jobs/{loc}?page={page}"
KW_URL = "https://www.karriere.at/jobs/{kw}/{loc}?page={page}"
DETAIL_URL = "https://www.karriere.at/jobs/{id}"
HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}


class KarriereSource(Source):
    name = "karriere"
    label = "karriere.at"

    def _queries(self, backfill: bool) -> list[tuple[str, int]]:
        """(list URL, pages). The generic list is date-sorted, but a day brings more ads than 3 pages hold, and
        old ads that never made it to the first pages are only reachable through keyword searches."""
        loc = self.params.get("location_slug", "wien")
        pages = int(self.params.get("backfill_pages" if backfill else "pages", 15 if backfill else 4))
        qs: list[tuple[str, int]] = [(LIST_URL.format(loc=loc, page="{page}"), pages)]
        kws = [str(k) for k in (self.params.get("keywords") or []) if str(k).strip()]
        if kws:
            if backfill:
                chosen, kp = kws, int(self.params.get("backfill_keyword_pages", 5))
            else:
                n = max(1, min(len(kws), int(self.params.get("keywords_per_run", 4))))
                slot = int(time.time() // max(60, self.interval))
                chosen = [kws[(slot * n + i) % len(kws)] for i in range(n)]
                kp = int(self.params.get("keyword_pages", 2))
            qs += [(KW_URL.format(kw=quote(k), loc=loc, page="{page}"), kp) for k in chosen]
        return qs

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        out: dict[str, RawJob] = {}
        self.begin()
        for url, pages in self._queries(backfill):
            for page in range(1, pages + 1):
                try:
                    data = http.get_json(url.format(page=page), headers=HEADERS, retry_block=(403,)).get("data", {})
                except http.HttpError as e:
                    if e.status in (404, 410):  # past the last page of this search
                        break
                    self.note_error(e, f"{url[-40:]} page {page}")
                    break
                except Exception as e:  # noqa: BLE001 - what is already in `out` is kept
                    self.note_error(e, f"{url[-40:]} page {page}")
                    break
                items = (data.get("jobsSearchList", {}).get("activeItems") or {}).get("items") or []
                if not items:
                    break
                for it in items:
                    j = it.get("jobsItem") if isinstance(it, dict) else None
                    if not j or not j.get("id"):
                        continue
                    jid = str(j["id"])
                    if jid in out:
                        continue
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
        elif int(job.meta.get("attempts", 0)) < 5:
            # never score on the list snippet (~200 chars) while the full text may still come: retry later
            raise RuntimeError("karriere.at: the detail page has no ad text yet")
        apply_url = ((content.get("applyAction") or {}).get("applyButton") or {}).get("applyUrl") or ""
        if apply_url and "karriere.at" not in apply_url:
            job.apply_url = clean_url(apply_url)
        job.needs_detail = False
        return job


_ID = re.compile(r"/jobs/(\d+)")
