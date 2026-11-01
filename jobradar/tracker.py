"""Application tracker: local mirror (SQLite) + Google Sheet via Apps Script, follow-ups."""
from __future__ import annotations

import logging
from datetime import timedelta

from .util import company_key, iso, local, now_utc, parse_iso

log = logging.getLogger("jobradar.tracker")

STATUS_LABEL = {
    "saved": "⭐ Saved",
    "applied": "✅ Applied",
    "confirmed": "📨 Confirmed",
    "assessment": "📝 Test / task",
    "interview": "🎤 Interview",
    "offer": "🏆 Offer",
    "rejected": "❌ Rejected",
    "no_response": "🔕 No response",
    "withdrawn": "↩️ Withdrawn",
}
LABEL_STATUS = {v: k for k, v in STATUS_LABEL.items()}
# order = how far the application got (never move backwards automatically)
PROGRESS = ["saved", "applied", "confirmed", "assessment", "interview", "offer"]
FINAL = {"rejected", "offer", "withdrawn"}
WAITING = {"applied", "confirmed"}

EMAIL_TO_STATUS = {
    "confirmation": "confirmed",
    "rejection": "rejected",
    "interview": "interview",
    "assessment": "assessment",
    "offer": "offer",
}


def label_to_status(label: str) -> str:
    """Sheet label → status key ("✅ Applied" → "applied"). Tolerates a missing emoji, other case and the key itself;
    returns "" for anything unknown (the caller then keeps the current status instead of storing garbage)."""
    label = (label or "").strip()
    if label in LABEL_STATUS:
        return LABEL_STATUS[label]
    low = label.lower()
    for k, v in STATUS_LABEL.items():
        if v.split(" ", 1)[-1].lower() in low or k in low.replace(" ", "_"):
            return k
    return ""


def should_change(current: str | None, new: str) -> bool:
    if not current:
        return True
    if new in ("rejected", "offer", "withdrawn", "no_response"):
        return current != new
    if current in FINAL:
        return False
    if current in PROGRESS and new in PROGRESS:
        return PROGRESS.index(new) > PROGRESS.index(current)
    return current != new


class Tracker:
    def __init__(self, settings, db, sheets):
        self.settings = settings
        self.db = db
        self.sheets = sheets
        self.followup_days = int(settings.get("tracker.followup_days", 7))

    def tracker_id_for_job(self, job_id: int) -> str:
        return f"j{job_id}"

    async def record_job_action(self, job: dict, status: str) -> str:
        """Create/update the tracker row for a radar job (Saved / Applied)."""
        a = job.get("analysis") or {}
        tid = self.tracker_id_for_job(job["id"])
        now = now_utc()
        fields = dict(
            job_id=job["id"],
            company=a.get("company_clean") or job.get("company"),
            title=a.get("title_clean") or job.get("title"),
            url=job.get("apply_url") or job.get("url"),
            status=status,
        )
        if status == "applied":
            fields["applied_at"] = iso(now)
            fields["followup_due"] = iso(now + timedelta(days=self.followup_days))
            fields["followup_count"] = 0
        self.db.upsert_application(tid, **fields)
        from .telegram_ui import source_label  # local import to avoid cycle

        row = {
            "tracker_id": tid,
            "status": STATUS_LABEL[status],
            "company": fields["company"],
            "title": fields["title"],
            "score": job.get("score"),
            "location": a.get("location") or job.get("location"),
            "hours": a.get("hours", ""),
            "salary": f"≈€{int(a['salary_20h_eur'])}" if a.get("salary_20h_eur") else (a.get("salary") or ""),
            "source": source_label(job),
            "apply_url": job.get("apply_url") or "",
            "url": job.get("url") or "",
        }
        if status == "applied":
            row["applied_at"] = local(now).strftime("%Y-%m-%d")
            row["followup_due"] = local(now + timedelta(days=self.followup_days)).strftime("%Y-%m-%d")
        await self.sheets.safe("upsert_tracker", {"row": row})
        return tid

    async def set_status(self, tid: str, status: str, note: str = "", **extra) -> None:
        app = self.db.get_application(tid) or {}
        fields = {"status": status, **extra}
        if status not in WAITING:
            fields["followup_due"] = None
        elif not app.get("followup_due") and int(app.get("followup_count") or 0) == 0:
            fields["followup_due"] = self._default_followup(app.get("applied_at") or extra.get("applied_at"))
        self.db.upsert_application(tid, **fields)
        row = {"tracker_id": tid, "status": STATUS_LABEL.get(status, status)}
        if "followup_due" in fields:
            row["followup_due"] = local(parse_iso(fields["followup_due"])).strftime("%Y-%m-%d") if fields["followup_due"] else ""
        for k in ("contact", "last_email", "company", "title", "applied_at"):
            if extra.get(k):
                row[k] = extra[k] if k != "applied_at" else local(parse_iso(extra[k])).strftime("%Y-%m-%d")
        if note:
            row["notes"] = note
        if not app and extra.get("company"):
            row.setdefault("source", "E-mail")
        await self.sheets.safe("upsert_tracker", {"row": row})

    async def remove_job_action(self, job: dict) -> None:
        tid = self.tracker_id_for_job(job["id"])
        if self.db.get_application(tid):
            await self.set_status(tid, "withdrawn", note="Undone in the bot")

    def _default_followup(self, applied_at: str | None) -> str:
        """applied + followup_days, but never in the past (then: tomorrow 10:00 reminder run)."""
        base = parse_iso(applied_at) if applied_at else None
        due = (base or now_utc()) + timedelta(days=self.followup_days)
        return iso(max(due, now_utc() + timedelta(hours=1)))

    def find_by_company(self, company: str) -> dict | None:
        key = company_key(company)
        if not key:
            return None
        best = None
        for app in self.db.applications():
            if company_key(app.get("company") or "") == key:
                if best is None or (app.get("updated") or "") > (best.get("updated") or ""):
                    best = app
        return best

    async def pull_from_sheet(self) -> int:
        """Sync statuses the owner changed by hand in the sheet (sheet wins)."""
        if not self.sheets.enabled:
            return 0
        rows = await self.sheets.call("get_tracker")
        n = 0
        for r in rows or []:
            tid = str(r.get("tracker_id") or "")
            if not tid:
                continue
            app = self.db.get_application(tid)
            status = label_to_status(str(r.get("status") or "")) or (app or {}).get("status") or "applied"
            fu = r.get("followup_due") or None
            if status in WAITING and not fu and (app or {}).get("followup_due"):
                fu = app["followup_due"]  # empty cell in the sheet ≠ "cancel the reminder" (change the status for that)
            elif status in WAITING and not fu and int((app or {}).get("followup_count") or 0) == 0:
                # a row added by hand without a follow-up date → give it one (and show it in the sheet)
                fu = self._default_followup(r.get("applied_at") or (app or {}).get("applied_at"))
                await self.sheets.safe("upsert_tracker", {"row": {
                    "tracker_id": tid, "followup_due": local(parse_iso(fu)).strftime("%Y-%m-%d")}})
            fields = {
                "company": r.get("company") or "",
                "title": r.get("title") or "",
                "url": r.get("apply_url") or r.get("url") or "",
                "status": status,
                "applied_at": r.get("applied_at") or (app or {}).get("applied_at"),
                "followup_due": fu if status in WAITING else None,
            }
            if not app or any(str(app.get(k) or "") != str(v or "") for k, v in fields.items() if k in ("status", "followup_due")):
                self.db.upsert_application(tid, **fields)
                n += 1
        return n

    def due_followups(self) -> list[dict]:
        now = now_utc()
        out = []
        for app in self.db.applications(WAITING):
            fu = parse_iso(app.get("followup_due"))
            if fu and fu <= now and int(app.get("followup_count") or 0) < 3:
                out.append(app)
        return out

    async def snooze(self, tid: str, days: int) -> None:
        due = iso(now_utc() + timedelta(days=days))
        self.db.upsert_application(tid, followup_due=due)
        await self.sheets.safe("upsert_tracker", {"row": {"tracker_id": tid,
                                                         "followup_due": local(parse_iso(due)).strftime("%Y-%m-%d")}})

    async def followup_sent(self, tid: str) -> None:
        app = self.db.get_application(tid) or {}
        cnt = int(app.get("followup_count") or 0) + 1
        due = iso(now_utc() + timedelta(days=self.followup_days)) if cnt < 2 else None
        self.db.upsert_application(tid, followup_count=cnt, followup_due=due)
        row = {"tracker_id": tid, "notes": f"{local(now_utc()):%d.%m} — follow-up #{cnt} sent",
               "followup_due": local(parse_iso(due)).strftime("%Y-%m-%d") if due else ""}
        await self.sheets.safe("upsert_tracker", {"row": row})
