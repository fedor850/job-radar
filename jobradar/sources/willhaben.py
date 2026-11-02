"""willhaben.at Jobs — Austria's biggest classifieds site; job search data is embedded as Next.js JSON."""
from __future__ import annotations

import json
import random
import re
import time

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_iso
from .base import Source

SEARCH = "https://www.willhaben.at/jobs/suche?location={loc}&page={page}{kw}"
DETAIL = "https://www.willhaben.at/jobs/job/{slug}/{id}"
HDR = {"Accept": "text/html", "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}
_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _next_data(html: str) -> dict:
    m = _ND.search(html)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


class WillhabenSource(Source):
    name = "willhaben"
    label = "willhaben Jobs"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        loc = self.params.get("location", "Wien")
        pages = int(self.params.get("backfill_pages" if backfill else "pages", 10 if backfill else 3))
        out: dict[str, RawJob] = {}
        self.begin()
        for kw in self.params.get("keywords") or [""]:
            for page in range(1, pages + 1):
                url = SEARCH.format(loc=loc, page=page, kw=f"&keyword={kw}" if kw else "")
                try:
                    # willhaben answers 403 at random → one retry with a fresh session (new UA / cookies)
                    html = http.get(url, headers=HDR, retry_block=(403,)).text
                    data = (_next_data(html).get("jobsSearchResultRoot") or {}).get("data") or {}
                except Exception as e:  # noqa: BLE001 - keep the pages already read
                    self.note_error(e, f"page {page}")
                    break
                entries = data.get("entries") or []
                for e in entries:
                    jid = str(e.get("id") or "")
                    if not jid or e.get("isExpired") or jid in out:
                        continue
                    modes = ", ".join(e.get("employmentModes") or [])
                    salary = ""
                    if e.get("salary"):
                        salary = f"€{e['salary']} {e.get('salaryTimeFrame') or ''}".strip()
                    out[jid] = RawJob(
                        source="willhaben",
                        external_id=jid,
                        title=e.get("title", ""),
                        company=(e.get("company") or {}).get("title", ""),
                        location=", ".join(l.get("name", "") for l in e.get("jobLocations") or []),
                        url=DETAIL.format(slug=e.get("slugTitle", "job"), id=jid),
                        posted_at=parse_iso((e.get("firstPublishDate") or "")[:19] + "+00:00") if e.get("firstPublishDate") else None,
                        description=e.get("description", ""),
                        meta={"employment_types": modes, "salary": salary, "start": e.get("employmentTime", "")},
                        needs_detail=True,
                    )
                if not entries:
                    break
                time.sleep(random.uniform(0.8, 1.6))
        return self.finish(list(out.values()))

    def fetch_detail(self, job: RawJob) -> RawJob:
        r = http.get(job.url, headers=HDR, ok=(200, 404, 410))
        if r.status_code != 200:
            job.meta["drop"] = True
            job.needs_detail = False
            return job
        props = _next_data(r.text)
        blob = json.dumps(props, ensure_ascii=False)
        # the advert body is HTML inside the page props; take the longest html-ish string
        best = ""
        for m in re.finditer(r'"(?:description|text|content|body)"\s*:\s*"((?:[^"\\]|\\.){300,})"', blob):
            s = json.loads(f'"{m.group(1)}"')
            if len(s) > len(best):
                best = s
        if best:
            job.description = html_to_text(best)
        for m in re.finditer(r'"(?:applicationUrl|externalApplicationUrl|applyUrl|url)"\s*:\s*"(https?://[^"]+)"', blob):
            u = m.group(1)
            if ("willhaben" not in u and not re.search(r"\.(png|jpe?g|svg|gif)(\?|$)", u)
                    and re.search(r"job|karriere|career|bewerb|apply|stelle|recruit|personio|workday|smartrecruiters", u, re.I)):
                job.apply_url = u
                break
        job.needs_detail = False
        return job
