"""Gmail → tracker: reads new e-mails via Apps Script, classifies job-related ones, updates statuses, notifies."""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from .tracker import EMAIL_TO_STATUS, STATUS_LABEL, should_change
from .util import clip, company_key, iso, local, now_utc, parse_iso

log = logging.getLogger("jobradar.mail")

KEYWORDS = re.compile(
    r"\b(application|applied|apply|applying|candida|interview|assessment|case study|online test|hirevue|"
    r"pymetrics|talent acquisition|recruit|hiring|vacanc|position|role|internship|praktikum|trainee|werkstudent|"
    r"offer|onboarding|contract|bewerbung|beworben|bewerber|vorstellungsgespr|gespr[aä]ch|einladung|absage|"
    r"zusage|stelle|leider|unfortunately|regret|not moving forward|thank you for your interest|"
    r"aplikacj|rekrutac|rozmow|zaproszeni|oferta|собеседован|ваканси|отклик)\w*",
    re.I,
)
ATS_SENDERS = re.compile(
    r"(myworkday|workday|greenhouse|lever\.co|smartrecruiters|successfactors|sapsf|personio|recruitee|workable|"
    r"teamtailor|join\.com|softgarden|umantis|csod|cornerstone|icims|taleo|oracle|avature|onlyfy|prescreen|"
    r"jobbase|dvinci|rexx|hokify|karriere\.at|jobs-noreply@linkedin|linkedin\.com|indeed|stepstone|zbp\.at|"
    r"phenom|eightfold|jobvite|bamboohr|recruiterbox|hrworks|talention)",
    re.I,
)
ALERT_SENDERS = re.compile(r"jobalerts-noreply@linkedin\.com|jobs-listings@linkedin\.com", re.I)
NOISE = re.compile(r"(newsletter|unsubscribe from this newsletter|webinar|sale|% off|rabatt)", re.I)

KIND_EMOJI = {
    "confirmation": "📨",
    "rejection": "📭",
    "interview": "🎉",
    "assessment": "📝",
    "offer": "🏆",
    "recruiter_outreach": "👋",
    "info_request": "📎",
}
KIND_TITLE = {
    "confirmation": "Отклик получен",
    "rejection": "Отказ",
    "interview": "Приглашение на интервью!",
    "assessment": "Тест / задание",
    "offer": "ОФФЕР!",
    "recruiter_outreach": "Рекрутер написал",
    "info_request": "Просят документы/информацию",
}


class MailSync:
    def __init__(self, settings, db, ai, sheets, tracker, notifier):
        self.settings = settings
        self.db = db
        self.ai = ai
        self.sheets = sheets
        self.tracker = tracker
        self.notifier = notifier

    def looks_relevant(self, e: dict) -> bool:
        sender = e.get("from", "")
        subject = e.get("subject", "")
        body = (e.get("body") or "")[:2500]
        if ATS_SENDERS.search(sender):
            return True
        known = {company_key(a.get("company") or "") for a in self.db.applications()}
        known.discard("")
        blob = f"{sender} {subject}".lower()
        if any(k and k in blob for k in known):
            return True
        if NOISE.search(subject) and not KEYWORDS.search(subject):
            return False
        return bool(KEYWORDS.search(subject) or len(KEYWORDS.findall(body)) >= 2)

    async def sync(self) -> int:
        if not self.sheets.enabled:
            return 0
        since = int(self.db.kv_get("gmail_since", 0) or 0)
        if not since:
            since = int(time.time()) - int(self.settings.get("mail.initial_days", 3)) * 86400
        emails = await self.sheets.call("get_emails", {"since": since - 900, "max": 40}, timeout=120)
        handled = 0
        max_epoch = since
        for e in sorted(emails or [], key=lambda x: x.get("epoch", 0)):
            max_epoch = max(max_epoch, int(e.get("epoch", 0)))
            if self.db.email_seen(e["id"]):
                continue
            if ALERT_SENDERS.search(e.get("from", "")):  # LinkedIn Job Alerts are read by the alerts channel
                self.db.save_email(e["id"], thread_id=e.get("thread_id"), received=e.get("date"),
                                   sender=e.get("from"), subject=e.get("subject"), kind="job_alert")
                continue
            if not self.looks_relevant(e):
                self.db.save_email(e["id"], thread_id=e.get("thread_id"), received=e.get("date"),
                                   sender=e.get("from"), subject=e.get("subject"), kind="skip")
                continue
            try:
                await self.handle(e)
                handled += 1
            except Exception as ex:  # noqa: BLE001
                log.exception("mail handle failed: %s", ex)
                fails = self.db.kv_get("mail_fail", {}) or {}
                fails[e["id"]] = int(fails.get(e["id"], 0)) + 1
                self.db.kv_set("mail_fail", dict(list(fails.items())[-50:]))
                if fails[e["id"]] >= 3:  # never let one bad e-mail block all the following ones
                    self.db.save_email(e["id"], thread_id=e.get("thread_id"), received=e.get("date"),
                                       sender=e.get("from"), subject=e.get("subject"), kind="error")
                    continue
                # leave unsaved → retried next sync (max_epoch overlap covers it)
                max_epoch = min(max_epoch, int(e.get("epoch", since)) - 1)
                break
        self.db.kv_set("gmail_since", max_epoch)
        self.db.kv_set("gmail_last_sync", iso(now_utc()))
        return handled

    async def handle(self, e: dict) -> None:
        apps = self.db.applications()
        tracker_list = [
            {"tracker_id": a["tracker_id"], "company": a.get("company"), "title": a.get("title"), "status": a.get("status")}
            for a in apps if a.get("status") not in ("withdrawn",)
        ]
        c = await self.ai.classify_email(e, tracker_list)
        kind = c.get("kind", "other")
        base = dict(thread_id=e.get("thread_id"), received=e.get("date"), sender=e.get("from"),
                    subject=e.get("subject"), kind=kind, company=c.get("company"), analysis={**c, "link": e.get("link")})
        if not c.get("job_related") or kind in ("job_alert", "other"):
            self.db.save_email(e["id"], **base)
            return

        tid = c.get("tracker_match") or ""
        app = self.db.get_application(tid) if tid else None
        if not app and c.get("company"):
            app = self.tracker.find_by_company(c["company"])
        new_status = EMAIL_TO_STATUS.get(kind)
        when = local(parse_iso(e.get("date")) or now_utc())
        last_email = f"{when:%d.%m} {KIND_TITLE.get(kind, kind)}: {clip(c.get('summary_ru',''), 140)}"
        contact = " ".join(x for x in [c.get("contact_name", ""), f"<{c['contact_email']}>" if c.get("contact_email") else ""] if x)

        if app:
            tid = app["tracker_id"]
            extra = {"last_email": last_email, "last_email_at": iso(now_utc())}
            if contact:
                extra["contact"] = contact
            if new_status and should_change(app.get("status"), new_status):
                await self.tracker.set_status(tid, new_status, **extra)
            else:
                self.db.upsert_application(tid, last_email_at=extra["last_email_at"], **({"contact": contact} if contact else {}))
                await self.sheets.safe("upsert_tracker", {"row": {"tracker_id": tid, "last_email": last_email,
                                                                 **({"contact": contact} if contact else {})}})
        elif new_status or kind == "recruiter_outreach":
            # application we did not track yet (applied elsewhere / before the radar) → create a row
            tid = f"e{e['id'][-10:]}"
            status = new_status or "saved"
            applied_at = iso(parse_iso(e.get("date")) or now_utc()) if status != "saved" else None
            followup_due = (iso(now_utc() + timedelta(days=self.tracker.followup_days))
                            if status in ("applied", "confirmed") else None)
            self.db.upsert_application(tid, company=c.get("company") or "", title=c.get("position") or "",
                                       status=status, applied_at=applied_at, contact=contact,
                                       followup_due=followup_due)
            await self.sheets.safe("upsert_tracker", {"row": {
                "tracker_id": tid, "status": STATUS_LABEL.get(status, status), "company": c.get("company") or "",
                "title": c.get("position") or "", "source": "Письмо", "last_email": last_email, "contact": contact,
                "applied_at": when.strftime("%Y-%m-%d") if applied_at else "",
            }})
        self.db.save_email(e["id"], tracker_id=tid or "", **base)
        await self.notifier.email_event(e, c, tid)
