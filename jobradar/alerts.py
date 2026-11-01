"""LinkedIn Job Alerts (e-mail) — a SECONDARY channel for LinkedIn jobs.

The owner saves LinkedIn searches with e-mail alerts; the Apps Script bridge returns those e-mails (action
"get_job_alerts", any Gmail category). Every job id from an alert becomes a RawJob with the SAME uid as the LinkedIn
scraper uses ("linkedin:<id>"), so:
  * a job the scraper already found is skipped silently (uid exists) — never sent twice;
  * a job the alert brings first goes through the normal pipeline (dedupe by company+title and by text against
    everything already sent, AI scoring, filters); when the scraper sees it later, the uid exists → skipped.
"""
from __future__ import annotations

import re

from .models import RawJob

JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/\s?]*-)?(\d{8,})")
VIEW = "https://www.linkedin.com/jobs/view/{id}/"
_NOISE = re.compile(
    r"^(view job|apply|easy apply|be an early applicant|actively (recruiting|hiring)|this company is actively|"
    r"promoted|\d+ (alumni|connections?|school alum)|.*\bapplicants?\b|.*\bago$|new$|top applicant|"
    r"your job alert|new jobs? match|see all jobs|-{3,}|={3,}|_{3,}|\.{3,}|https?://|unsubscribe|you are receiving|"
    r"manage (job )?alerts|.*job alert for|search for more|jobs similar to|get the new linkedin|linkedin corporation|"
    r"©|\d+\+? new jobs|.*\bresume\b|.*\bprofile\b$|salary|€|\$)", re.I)


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("‌", "").replace(" ", " ")).strip(" \t-–|·•")


def parse_alert(body: str) -> list[dict]:
    """Extract jobs from the plain-text body of a LinkedIn job-alert e-mail.

    Layout per job (varies a bit): title / company / location / optional badges / "View job: <url>".
    We take the job id from the link and the 1–3 meaningful lines right above it (top → bottom = title,
    company, location). Title/company are refreshed from the LinkedIn page when the details are loaded."""
    lines = (body or "").splitlines()
    out: dict[str, dict] = {}
    for i, raw in enumerate(lines):
        m = JOB_LINK.search(raw)
        if not m:
            continue
        jid = m.group(1)
        if jid in out:
            continue
        ctx: list[str] = []
        j = i - 1
        while j >= 0 and len(ctx) < 3 and i - j <= 9:
            t = _clean(lines[j])
            j -= 1
            if not t:
                if ctx:  # blank line above the block → block starts here
                    break
                continue
            if JOB_LINK.search(t) or _NOISE.search(t) or len(t) > 140:
                if ctx:
                    break
                continue
            ctx.insert(0, t)
        # the text before the link on the same line ("Consulting Intern: https://…") also counts
        same = _clean(raw[: m.start()].rsplit("http", 1)[0]).rstrip(":")
        if same and not _NOISE.search(same) and not ctx:
            ctx = [same]
        title = ctx[0] if ctx else ""
        company = ctx[1] if len(ctx) > 1 else ""
        location = ctx[2] if len(ctx) > 2 else ""
        out[jid] = {"id": jid, "title": title, "company": company, "location": location}
    return list(out.values())


def alert_jobs(emails: list[dict]) -> list[RawJob]:
    jobs: dict[str, RawJob] = {}
    for e in emails:
        for j in parse_alert(e.get("body") or ""):
            if j["id"] in jobs or not j["title"]:
                continue
            jobs[j["id"]] = RawJob(
                source="linkedin", external_id=j["id"], title=j["title"], company=j["company"],
                location=j["location"] or "Vienna", url=VIEW.format(id=j["id"]),
                meta={"from_alert": True, "alert_subject": (e.get("subject") or "")[:120]}, needs_detail=True,
            )
    return list(jobs.values())
