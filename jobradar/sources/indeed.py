"""Indeed Austria via the JobSpy library (uses Indeed's mobile GraphQL API; gives direct company URLs)."""
from __future__ import annotations

import time

import math
from datetime import datetime, timezone

from ..models import RawJob
from ..util import clean_url
from .base import Source


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v)


class IndeedSource(Source):
    name = "indeed"
    label = "Indeed"
    _last: dict = {}

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        from jobspy import scrape_jobs  # heavy import, keep lazy

        hours = int(self.params.get("backfill_hours" if backfill else "hours_old", 168 if backfill else 2))
        results = int(self.params.get("backfill_results" if backfill else "results_wanted", 400 if backfill else 80))
        out: dict[str, RawJob] = {}
        self.begin()
        for q in self.params.get("queries") or [{"search_term": "", "location": "Wien"}]:
            gap = int(q.get("min_interval_sec", 0))
            qkey = q.get("search_term", "")
            if gap and not backfill:
                if time.time() - IndeedSource._last.get(qkey, 0) < gap:
                    continue
                IndeedSource._last[qkey] = time.time()
            try:
                df = scrape_jobs(
                    site_name=["indeed"],
                    search_term=q.get("search_term", ""),
                    location=q.get("location", "Wien"),
                    distance=int(q.get("distance", 25)),
                    results_wanted=int(q.get("results", results)) if not backfill else results,
                    hours_old=int(q.get("hours_old", hours)) if not backfill else hours,
                    country_indeed=self.params.get("country", "Austria"),
                    description_format="markdown",
                    verbose=0,
                )
            except Exception as e:  # noqa: BLE001 - the other queries still count
                self.note_error(e, f"query {q.get('search_term', '')!r}")
                continue
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                jid = _s(r.get("id"))
                if not jid:
                    continue
                posted = r.get("date_posted")
                posted_dt = None
                if posted is not None and _s(posted):
                    try:
                        posted_dt = datetime.fromisoformat(str(posted)).replace(tzinfo=timezone.utc)
                    except ValueError:
                        posted_dt = None
                salary = ""
                mn, mx = r.get("min_amount"), r.get("max_amount")
                if _s(mn) or _s(mx):
                    salary = f"{_s(mn)}–{_s(mx)} {_s(r.get('currency'))} / {_s(r.get('interval'))}".strip()
                direct = clean_url(_s(r.get("job_url_direct")))
                out[jid] = RawJob(
                    source="indeed",
                    external_id=jid,
                    title=_s(r.get("title")),
                    company=_s(r.get("company")),
                    location=_s(r.get("location")),
                    url=_s(r.get("job_url")),
                    apply_url=direct,
                    description=_s(r.get("description")),
                    posted_at=posted_dt,
                    meta={
                        "job_type": _s(r.get("job_type")),
                        "salary": salary,
                        "is_remote": bool(r.get("is_remote")) if _s(r.get("is_remote")) else False,
                        "company_url": _s(r.get("company_url_direct")),
                    },
                )
        return self.finish(list(out.values()))
