"""Telegram bot: notifier, commands, buttons and all scheduled jobs. Entry point: `python -m jobradar`."""
from __future__ import annotations

import asyncio
import calendar
import io
import logging
import random
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, TypeHandler

from . import __version__
from .ai import AI
from .config import Settings, load_settings
from .db import DB
from .links import classify_link
from .mail import KIND_EMOJI, KIND_TITLE, MailSync
from .pipeline import Pipeline
from .sheets import Sheets
from .sources.boards import BoardsSource, detect_board, probe_board
from .sources.indeed import IndeedSource
from .sources.karriere import KarriereSource
from .sources.linkedin import LinkedInSource
from .sources.zbp import ZbpSource
from .sources.apis import CareerjetSource, JoobleSource
from .sources.jobsat import JobsAtSource
from .sources.willhaben import WillhabenSource
from .sources.derstandard import DerStandardSource
from .sources.sites import SiteSource
from .telegram_ui import (
    SITE_LABELS,
    SKIP_REASON_TEXT,
    claude_export,
    docs_label,
    job_keyboard,
    render_job,
    skip_reasons_keyboard,
    skipped_keyboard,
    skipped_text,
    source_label,
    status_line,
)
from .tracker import STATUS_LABEL, Tracker
from .util import clip, company_key, esc, human_age, iso, local, now_utc, parse_iso

log = logging.getLogger("jobradar.bot")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
SOURCE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "karriere": "karriere.at", "zbp": "WU ZBP",
                "jobsat": "jobs.at", "willhaben": "willhaben Jobs", "derstandard": "DER STANDARD Jobs", "jooble": "Jooble", "careerjet": "Careerjet", "gmail": "Mail (Gmail)",
                "boards": "Company sites", "linkedin_alerts": "LinkedIn Job Alerts (mail)"}


# ====================================================================== notifier
class Notifier:
    """Everything that sends/edits Telegram messages."""

    def __init__(self, settings: Settings, db: DB):
        self.settings = settings
        self.db = db
        self.app: Application | None = None
        self._send_lock = asyncio.Lock()

    @property
    def chat_id(self) -> int | None:
        return self.settings.owner_chat_id or self.db.kv_get("owner_chat_id")

    @property
    def ready(self) -> bool:
        return self.app is not None and self.chat_id is not None

    @staticmethod
    def source_label(job: dict) -> str:
        return source_label(job)

    async def _call(self, fn, *args, **kwargs):
        """Serialise sends (≈1 msg/s per chat) and retry on flood control / network blips."""
        async with self._send_lock:
            for attempt in range(4):
                try:
                    res = await fn(*args, **kwargs)
                    await asyncio.sleep(0.4)
                    return res
                except RetryAfter as e:
                    ra = e.retry_after
                    wait = ra.total_seconds() if isinstance(ra, timedelta) else float(ra)
                    await asyncio.sleep(wait + 1)
                except (TimedOut, NetworkError):
                    await asyncio.sleep(2 + attempt * 3)
            return None

    async def text(self, text: str, silent: bool = False, kb: InlineKeyboardMarkup | None = None) -> int | None:
        if not self.ready:
            log.info("no owner chat yet; message dropped: %s", text[:80])
            return None
        msg = await self._call(self.app.bot.send_message, chat_id=self.chat_id, text=text[:4096],
                               parse_mode=ParseMode.HTML, reply_markup=kb, disable_notification=silent,
                               link_preview_options=NO_PREVIEW)
        return msg.message_id if msg else None

    async def send_job(self, job: dict, silent: bool = False) -> int | None:
        if not self.ready:
            return None
        text = render_job(job)
        kb = job_keyboard(self.settings, job, "new")
        try:
            msg = await self._call(self.app.bot.send_message, chat_id=self.chat_id, text=text,
                                   parse_mode=ParseMode.HTML, reply_markup=kb, disable_notification=silent,
                                   link_preview_options=NO_PREVIEW)
        except BadRequest as e:
            log.warning("send_job BadRequest (%s) — retrying without buttons", e)
            msg = await self._call(self.app.bot.send_message, chat_id=self.chat_id, text=text,
                                   parse_mode=ParseMode.HTML, disable_notification=silent,
                                   link_preview_options=NO_PREVIEW)
        return msg.message_id if msg else None

    def _state_for(self, job: dict) -> tuple[str, str]:
        st = job.get("user_status") or ""
        meta = job.get("meta") or {}
        when = parse_iso(meta.get("user_status_at"))
        if st == "applied":
            return "applied", status_line("applied", when)
        if st == "saved":
            return "saved", status_line("saved", when)
        return "new", ""

    async def refresh_job(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job or not job.get("tg_message_id") or not self.ready:
            return
        if job.get("user_status") == "skipped":
            text = skipped_text(job, job.get("user_reason") or "")
            kb = skipped_keyboard(job_id)
        else:
            state, line = self._state_for(job)
            if (job.get("meta") or {}).get("company_link_found") and classify_link(job.get("apply_url") or "") == "company":
                line = (line + "\n" if line else "") + "🏢 <i>Found the job on the company's own site — the button below goes there.</i>"
            text = render_job(job, line)
            kb = job_keyboard(self.settings, job, state)
        try:
            await self._call(self.app.bot.edit_message_text, chat_id=self.chat_id, message_id=job["tg_message_id"],
                             text=text, parse_mode=ParseMode.HTML, reply_markup=kb, link_preview_options=NO_PREVIEW)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                log.info("edit failed for job %s: %s", job_id, e)

    async def source_health(self, name: str, h: dict) -> None:
        threshold = int(self.settings.get(f"health.alert_after_fails.{name}",
                                          self.settings.get("health.alert_after_fails.default", 4)))
        label = SOURCE_NAMES.get(name, name)
        if h.get("fails", 0) >= threshold and not h.get("alerted"):
            self.db.set_alerted(name, 1)
            await self.text(f"⚠️ Source <b>{esc(label)}</b> has failed {h['fails']} runs in a row.\n"
                            f"<code>{esc(clip(h.get('last_error') or '', 300))}</code>\n"
                            f"The other sources are working. I'll tell you when it recovers.")
        elif h.get("fails", 0) == 0 and h.get("alerted"):
            self.db.set_alerted(name, 0)
            await self.text(f"✅ Source <b>{esc(label)}</b> is working again.", silent=True)

    async def email_event(self, e: dict, c: dict, tid: str) -> None:
        kind = c.get("kind", "")
        lines = [f"{KIND_EMOJI.get(kind, '📧')} <b>{esc(KIND_TITLE.get(kind, 'E-mail'))}</b> · "
                 f"{esc(c.get('company') or '')}" + (f" — {esc(clip(c.get('position') or '', 80))}" if c.get("position") else "")]
        summary = c.get("summary") or c.get("summary_ru")  # *_ru: e-mails classified before v1.1
        action = c.get("action") or c.get("action_ru")
        if summary:
            lines.append(esc(summary))
        if action:
            lines.append(f"👉 <b>{esc(action)}</b>")
        if c.get("deadline"):
            lines.append(f"⏳ Deadline: {esc(c['deadline'])}")
        if c.get("event_start"):
            st = parse_iso(c["event_start"])
            when = local(st).strftime("%a %d.%m %H:%M") if st else c["event_start"]
            lines.append(f"🗓 {esc(when)}" + (f" · {esc(clip(c.get('event_location') or '', 80))}" if c.get("event_location") else ""))
        if tid:
            app = self.db.get_application(tid) or {}
            if app.get("status"):
                lines.append(f"📊 Tracker: {esc(STATUS_LABEL.get(app['status'], app['status']))}")
        buttons = []
        if e.get("link"):
            buttons.append(InlineKeyboardButton("📧 Open e-mail", url=e["link"]))
        if c.get("event_start"):
            buttons.append(InlineKeyboardButton("📅 Add to calendar", callback_data=f"ev:{e['id'][:40]}"))
        kb = InlineKeyboardMarkup([buttons]) if buttons else None
        await self.text("\n".join(lines), kb=kb)


# ====================================================================== the app
class JobRadar:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DB(settings.db_path)
        self.notifier = Notifier(settings, self.db)
        self.ai = AI(settings, self.db)
        self.sheets = Sheets(settings)
        self.tracker = Tracker(settings, self.db, self.sheets)
        self.sources = self._build_sources()
        self.pipeline = Pipeline(settings, self.db, self.ai, self.sheets, self.notifier, self.sources)
        self.mail = MailSync(settings, self.db, self.ai, self.sheets, self.tracker, self.notifier)
        self.started = now_utc()
        self.backfill_running = False

    def _build_sources(self) -> dict[str, Any]:
        s = self.settings
        out: dict[str, Any] = {}
        defs = {
            "linkedin": LinkedInSource,
            "indeed": IndeedSource,
            "karriere": KarriereSource,
            "zbp": ZbpSource,
            "jobsat": JobsAtSource,
            "willhaben": WillhabenSource,
            "derstandard": DerStandardSource,
            "jooble": JoobleSource,
            "careerjet": CareerjetSource,
        }
        for name, cls in defs.items():
            params = s.get(f"sources.{name}", {}) or {}
            if params.get("enabled", True):
                out[name] = cls(s, params, db=self.db) if name in ("jooble", "careerjet") else cls(s, params)
        for site in s.get("sources.sites", []) or []:
            if isinstance(site, dict) and site.get("enabled", True) and site.get("name"):
                out[site["name"]] = SiteSource(s, site, db=self.db)
                SOURCE_NAMES.setdefault(site["name"], site.get("label", site["name"]))
                SITE_LABELS[site["name"]] = site.get("label", site["name"])
        bparams = s.get("sources.boards", {}) or {}
        if bparams.get("enabled", True):
            out["boards"] = BoardsSource(s, bparams, db=self.db)
        return out

    def seed_boards(self) -> None:
        n = 0
        for b in self.settings.get("companies", []) or []:
            kind, key = b.get("kind"), b.get("key")
            if not kind or not key:
                continue
            params = {k: v for k, v in b.items() if k not in ("kind", "key", "company")}
            self.db.upsert_board(kind, key, b.get("company", key), "seed", params)
            n += 1
        log.info("seeded %d company boards", n)

    # ------------------------------------------------------------------ guards
    def is_owner(self, update: Update) -> bool:
        chat = update.effective_chat
        return chat is not None and self.notifier.chat_id is not None and chat.id == self.notifier.chat_id

    # ------------------------------------------------------------------ commands
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        first_owner = self.notifier.chat_id is None
        if first_owner or (self.notifier.chat_id == chat.id and not self.db.kv_get("welcomed")):
            if first_owner:
                self.db.kv_set("owner_chat_id", chat.id)
                log.info("owner chat bound: %s", chat.id)
            self.db.kv_set("welcomed", True)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Send open jobs from the last 10 days", callback_data="bf:10")]])
            first_name = (self.settings.get("candidate.name") or "").split(" ")[0]
            await update.message.reply_text(
                f"👋 Hi{(', ' + first_name) if first_name else ''}! I'm <b>Job Radar</b>.\n\n"
                "Every few minutes I check LinkedIn, Indeed, job boards and hundreds of company career sites, "
                "score every new job against your profile and send you the ones that fit right away — with the apply "
                "link, why it fits and the risks.\n\n"
                "Every job has buttons: ✅ <b>Applied</b> (I add it to the tracker and remind you to follow up in 7 days), "
                "⭐ <b>Save</b>, 🙅 <b>Not interested</b> (I learn from your reasons) and 📋 <b>For Claude</b> "
                f"(the job as a file for your CV and letter in the Claude project {self.settings.get('candidate.claude_project', 'Claude')} — "
                "exactly the documents the ad asks for).\n\n"
                "Commands: /help. Do you want the jobs that are already open right now?",
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
            held = await self.pipeline.release_held()
            if held:
                await self.notifier.text(f"📦 Sent {held} jobs found before the bot started.", silent=True)
            return
        if not self.is_owner(update):
            await update.message.reply_text("This is a private bot.")
            return
        await self.cmd_help(update, ctx)

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        await update.message.reply_text(
            "<b>Job Radar commands</b>\n"
            "/status — sources, AI spend, settings\n"
            "/stats — statistics for 7 days (/stats 30 — for 30)\n"
            "/minscore 50 — minimum score for notifications (now "
            f"{self.pipeline.min_score})\n"
            "/prefs — your wishes for the AI (/prefs no call-centre jobs; /prefs clear)\n"
            "/pause and /resume — pause notifications\n"
            "/backfill 14 — send jobs still open from the last N days; /more — the next batch\n"
            "/tracker — link to the Google Sheet with your applications\n"
            "/addcompany &lt;link&gt; — watch a company (link to one of its jobs or its career page)\n"
            "/sources — which company career sites I watch\n"
            "/followups — applications due for a follow-up\n"
            "/test — send a sample card",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        lines = [f"🛰 <b>Job Radar v{__version__}</b> · running for {human_age(self.started).replace(' ago', '')}"]
        lines.append(("⏸ <b>Paused</b>" if self.pipeline.paused else "▶️ Active") +
                     f" · min. score: <b>{self.pipeline.min_score}</b>")
        lines.append("")
        day = now_utc() - timedelta(hours=24)
        for h in self.db.health_all():
            name = h["name"]
            ok = parse_iso(h.get("last_ok"))
            icon = "🟢" if h.get("fails", 0) == 0 else ("🟠" if h.get("fails", 0) < 4 else "🔴")
            lines.append(f"{icon} {SOURCE_NAMES.get(name, name)}: checked {human_age(ok) if ok else '—'}"
                         + (f" · failures in a row: {h['fails']}" if h.get("fails") else ""))
        boards = self.db.boards(enabled_only=False)
        seed = sum(1 for b in boards if b["origin"] == "seed")
        learned = sum(1 for b in boards if b["origin"] != "seed" and b["enabled"])
        failing = sum(1 for b in boards if b["enabled"] and (b.get("fails") or 0) >= 3)
        lines.append(f"🏢 Company sites: {seed} seeded + {learned} found automatically"
                     + (f" ({failing} failing)" if failing else ""))
        st = self.db.stage_counts(day)
        lines.append("")
        lines.append(f"Last 24 h: {sum(st.values())} new jobs, <b>{st.get('sent', 0)}</b> sent, "
                     f"{st.get('triage_reject', 0) + st.get('blocked', 0) + st.get('below_threshold', 0)} filtered out by the AI, "
                     f"{st.get('duplicate', 0)} duplicates" + (f", {st['error']} errors" if st.get("error") else ""))
        month = local(now_utc()).strftime("%Y-%m-01")
        today = now_utc().strftime("%Y-%m-%d")
        u_today = self.db.usage_since(today)
        u_month = self.db.usage_since(month)
        lines.append(f"💸 Claude API: today ${u_today['cost']:.3f}, this month ${u_month['cost']:.2f}")
        gm = parse_iso(self.db.kv_get("gmail_last_sync"))
        lines.append(f"📧 Mail: {'checked ' + human_age(gm) if gm else 'not connected' if not self.sheets.enabled else 'not checked yet'}")
        sheet = self.db.kv_get("sheet_url")
        if sheet:
            lines.append(f'📊 <a href="{esc(sheet)}">Application tracker</a>')
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        days = 7
        if ctx.args and ctx.args[0].isdigit():
            days = max(1, min(365, int(ctx.args[0])))
        await update.message.reply_text(self.report_text(days), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)

    def report_text(self, days: int, nudge: bool = True) -> str:
        since = now_utc() - timedelta(days=days)
        st = self.db.stage_counts(since)
        sent_by_src = self.db.source_counts(since, "sent")
        rows = self.db.query("SELECT user_status, COUNT(*) n FROM jobs WHERE sent_at>=? GROUP BY user_status", (iso(since),))
        us = {r["user_status"] or "none": r["n"] for r in rows}
        hot = self.db.one("SELECT COUNT(*) n FROM jobs WHERE sent_at>=? AND score>=80", (iso(since),))["n"]
        apps = self.db.applications()
        def cnt(status: str) -> int:
            return sum(1 for a in apps if a.get("status") == status and (parse_iso(a.get("updated")) or since) >= since)
        cost = self.db.usage_since((now_utc() - timedelta(days=days)).strftime("%Y-%m-%d"))["cost"]
        lines = [f"📊 <b>Statistics for {days} days</b>",
                 f"New jobs checked: {sum(st.values())}",
                 f"Sent: <b>{st.get('sent', 0)}</b> (🔥 80+: {hot})",
                 f"You: ✅ applied {us.get('applied', 0)} · ⭐ saved {us.get('saved', 0)} · 🙅 skipped {us.get('skipped', 0)}",
                 f"Replies: 📝 tests {cnt('assessment')} · 🎤 interviews {cnt('interview')} · 🏆 offers {cnt('offer')} · ❌ rejections {cnt('rejected')}"]
        if sent_by_src:
            src = ", ".join(f"{SOURCE_NAMES.get(k, k)} {v}" for k, v in sorted(sent_by_src.items(), key=lambda x: -x[1]))
            lines.append(f"Sent by source: {esc(src)}")
        lines.append(f"💸 Claude API: ${cost:.2f}")
        if nudge:
            rows = self.db.query(
                "SELECT * FROM jobs WHERE sent_at>=? AND score>=80 AND (user_status IS NULL OR user_status='') "
                "ORDER BY score DESC LIMIT 5", (iso(since),))
            if rows:
                lines.append("\n🔥 <b>Strong jobs without a reaction:</b>")
                for r in rows:
                    j = self.db.row_to_job(r)
                    a = j.get("analysis") or {}
                    url = j.get("apply_url") or j.get("url")
                    lines.append(f'• {j["score"]} · <a href="{esc(url)}">{esc(clip(a.get("title_clean") or j["title"], 60))}</a>'
                                 f' — {esc(clip(a.get("company_clean") or j["company"], 30))}')
        return "\n".join(lines)

    async def cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        self.db.kv_set("paused", True)
        await update.message.reply_text("⏸ Paused. I keep searching and scoring but don't send anything. /resume to continue.")

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        self.db.kv_set("paused", False)
        n = await self.pipeline.release_held()
        await update.message.reply_text("▶️ Sending jobs again." + (f" I'll send the {n} held ones (silently)." if n else ""))

    async def cmd_minscore(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        if ctx.args and ctx.args[0].isdigit():
            v = max(0, min(100, int(ctx.args[0])))
            self.db.kv_set("min_score", v)
            await update.message.reply_text(f"Done: sending jobs with a score of <b>{v}</b> or more.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(
                f"Currently sending from <b>{self.pipeline.min_score}</b>/100. Change it: <code>/minscore 55</code>",
                parse_mode=ParseMode.HTML)

    async def cmd_prefs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        prefs: list[str] = self.db.kv_get("prefs", []) or []
        arg = " ".join(ctx.args or []).strip()
        if arg.lower() == "clear":
            self.db.kv_set("prefs", [])
            await update.message.reply_text("Wishes cleared.")
            return
        if arg.lower().startswith("del ") and arg[4:].strip().isdigit():
            i = int(arg[4:].strip()) - 1
            if 0 <= i < len(prefs):
                removed = prefs.pop(i)
                self.db.kv_set("prefs", prefs)
                await update.message.reply_text(f"Removed: {removed}")
            return
        if arg:
            prefs.append(arg[:300])
            self.db.kv_set("prefs", prefs)
            await update.message.reply_text(f"Noted. The AI will take this into account: “{arg[:300]}”")
            return
        if not prefs:
            await update.message.reply_text(
                "No wishes yet. Example: <code>/prefs more start-ups, fewer banks</code>\n"
                "Remove one: <code>/prefs del 1</code>, clear all: <code>/prefs clear</code>", parse_mode=ParseMode.HTML)
            return
        await update.message.reply_text("Your wishes:\n" + "\n".join(f"{i+1}. {p}" for i, p in enumerate(prefs)))

    async def cmd_tracker(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        url = self.db.kv_get("sheet_url")
        await update.message.reply_text(f'📊 <a href="{esc(url)}">Application tracker (Google Sheet)</a>' if url
                                        else "The tracker is not connected yet.", parse_mode=ParseMode.HTML)

    async def cmd_sources(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        boards = self.db.boards(enabled_only=False)
        lines = [f"🏢 <b>Company sites ({sum(1 for b in boards if b['enabled'])} active)</b>"]
        for b in sorted(boards, key=lambda x: (x["origin"] != "seed", (x.get("company") or x["key"]).lower())):
            if not b["enabled"]:
                continue
            mark = "⚠️" if (b.get("fails") or 0) >= 3 else ("🆕" if b["origin"] != "seed" else "•")
            lines.append(f"{mark} {esc(b.get('company') or b['key'])} <i>({b['kind']})</i>")
        text = "\n".join(lines)
        for i in range(0, len(text), 3900):
            await update.message.reply_text(text[i:i + 3900], parse_mode=ParseMode.HTML)

    async def cmd_addcompany(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        url = (ctx.args or [""])[0]
        b = detect_board(url)
        if not b:
            await update.message.reply_text(
                "I don't recognise the career system behind this link. Links to jobs or job lists on Workday, "
                "SmartRecruiters, Greenhouse, Lever, Ashby, Personio, Recruitee or Workable work. Send a link to any "
                "job of this company.")
            return
        kind, key, params = b
        n = await asyncio.to_thread(probe_board, kind, key, params)
        if n is None:
            await update.message.reply_text("The system is recognised, but the page does not answer. Check the link.")
            return
        company = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else key.split("/")[0]
        self.db.upsert_board(kind, key, company, "manual", params)
        await update.message.reply_text(f"✅ Added {company} ({kind}). Open in your area right now: {n}. "
                                        f"I'll catch this company's new jobs within a few minutes.")

    async def cmd_backfill(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 14
        await self.start_backfill(days)

    async def start_backfill(self, days: int) -> None:
        if self.backfill_running:
            await self.notifier.text("Already collecting — give me a couple of minutes.")
            return
        days = min(days, int(self.settings.get("general.max_age_days", 10)))
        self.backfill_running = True
        await self.notifier.text(f"🔎 Collecting jobs still open from the last {days} days from all sources and scoring them. "
                                 f"This takes 5–15 minutes; then I'll send the best ones (silently).")
        try:
            self.settings.cfg.setdefault("general", {})["backfill_days"] = days
            for name in self.sources:
                await self.pipeline.run_source(name, backfill=True)
            await self.send_more(int(self.settings.get("general.backfill_batch", 25)), first=True)
        finally:
            self.backfill_running = False

    async def send_more(self, n: int, first: bool = False) -> None:
        rows = self.db.query("SELECT id FROM jobs WHERE stage='held_backfill' ORDER BY score DESC, id LIMIT ?", (n,))
        for r in rows:
            await self.pipeline.deliver(r["id"], silent=True)
        left = self.db.one("SELECT COUNT(*) n FROM jobs WHERE stage='held_backfill'")["n"]
        if first and not rows:
            await self.notifier.text("Done. No open jobs above the threshold — I'll send new ones as soon as they appear.")
        elif left:
            await self.notifier.text(f"📦 Sent the {len(rows)} best. {left} more good ones are waiting — /more")
        elif rows:
            await self.notifier.text(f"📦 Done: sent {len(rows)} open jobs. From now on only new ones, as soon as they appear.")

    async def cmd_more(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        await self.send_more(20)

    async def cmd_followups(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        due = self.tracker.due_followups()
        if not due:
            waiting = self.db.applications({"applied", "confirmed"})
            await update.message.reply_text(f"No follow-ups due. Waiting for a reply: {len(waiting)}.")
            return
        for app in due:
            await self.send_followup_reminder(app)

    async def cmd_test(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        row = self.db.one("SELECT * FROM jobs WHERE analysis IS NOT NULL AND analysis<>'' ORDER BY score DESC, id DESC LIMIT 1")
        if not row:
            await update.message.reply_text("No scored jobs yet — wait for the first run (a few minutes).")
            return
        job = self.db.row_to_job(row)
        await self.notifier.text("🧪 Sample card (the best scored job):", silent=True)
        if job.get("stage") == "sent":
            await self.notifier.send_job(job, silent=True)
        else:
            await self.pipeline.deliver(job["id"], silent=True)

    # ------------------------------------------------------------------ buttons
    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not self.is_owner(update):
            await q.answer("This is a private bot.")
            return
        data = q.data or ""
        try:
            action, _, rest = data.partition(":")
            if action in ("ap", "sv", "sk", "sr", "un", "bk", "cl"):
                await self.on_job_button(q, action, rest)
            elif action in ("fd", "fs", "fn", "fx", "fo", "fw"):
                await self.on_followup_button(q, action, rest)
            elif action == "ev":
                await self.on_calendar(q, rest)
            elif action == "bf":
                await q.answer("Starting…")
                asyncio.get_running_loop().create_task(self.start_backfill(int(rest or 14)))
            else:
                await q.answer()
        except Exception as e:  # noqa: BLE001
            log.exception("callback %s failed", data)
            await q.answer(f"Error: {str(e)[:150]}", show_alert=True)

    async def on_job_button(self, q, action: str, rest: str) -> None:
        parts = rest.split(":")
        job_id = int(parts[0])
        job = self.db.get_job(job_id)
        if not job:
            await q.answer("Job not found")
            return
        meta = job.get("meta") or {}
        a = job.get("analysis") or {}
        title = a.get("title_clean") or job["title"]
        company = a.get("company_clean") or job["company"]
        if action == "ap":
            meta["user_status_at"] = iso(now_utc())
            self.db.update_job(job_id, user_status="applied", meta=meta)
            self.db.add_feedback(job_id, "applied", "", title, company)
            await q.answer("✅ Added to the tracker. I'll remind you to follow up in 7 days.")
            await self.tracker.record_job_action(self.db.get_job(job_id), "applied")
        elif action == "sv":
            meta["user_status_at"] = iso(now_utc())
            self.db.update_job(job_id, user_status="saved", meta=meta)
            self.db.add_feedback(job_id, "saved", "", title, company)
            await q.answer("⭐ Saved to the tracker")
            await self.tracker.record_job_action(self.db.get_job(job_id), "saved")
        elif action == "sk":
            await q.answer()
            await q.edit_message_reply_markup(reply_markup=skip_reasons_keyboard(job_id))
            return
        elif action == "sr":
            code = parts[1] if len(parts) > 1 else "other"
            self.db.update_job(job_id, user_status="skipped", user_reason=code)
            self.db.add_feedback(job_id, "skip", SKIP_REASON_TEXT.get(code, code).split(" ", 1)[-1], title, company)
            await q.answer("Got it, noted")
        elif action == "un":
            prev = job.get("user_status")
            self.db.update_job(job_id, user_status="", user_reason="")
            self.db.execute("DELETE FROM feedback WHERE id = (SELECT MAX(id) FROM feedback WHERE job_id=?)", (job_id,))
            if prev in ("applied", "saved"):
                await self.tracker.remove_job_action(job)
            await q.answer("Restored")
        elif action == "bk":
            await q.answer()
        elif action == "cl":
            await q.answer("Sending the file…")
            await self.send_claude_file(job)
            return
        await self.notifier.refresh_job(job_id)

    async def send_claude_file(self, job: dict) -> None:
        a = job.get("analysis") or {}
        name = f"{a.get('company_clean') or job['company']} - {a.get('title_clean') or job['title']}"
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_.()").strip()[:90] or "job"
        key = company_key(a.get("company_clean") or job.get("company") or "")
        earlier = [x for x in self.db.applications() if key and company_key(x.get("company") or "") == key
                   and x.get("job_id") != job["id"] and x.get("status") not in ("withdrawn", "saved")]
        data = claude_export(job, earlier).encode("utf-8")
        await self.notifier._call(
            self.notifier.app.bot.send_document, chat_id=self.notifier.chat_id,
            document=io.BytesIO(data), filename=f"{safe}.md",
            caption=(f"📋 Forward this file to a new chat in your Claude project <b>{self.settings.get('candidate.claude_project', 'Claude')}</b> "
                     "(or open a chat in the project and attach it). Claude checks the fit, asks its questions and prepares "
                     f"what the ad asks for: <b>{docs_label(job)}</b>."),
            parse_mode=ParseMode.HTML,
        )

    # ------------------------------------------------------------------ follow-ups
    async def send_followup_reminder(self, app: dict) -> None:
        tid = app["tracker_id"]
        applied = parse_iso(app.get("applied_at"))
        days = (now_utc() - applied).days if applied else None
        n = int(app.get("followup_count") or 0)
        text = (f"⏰ <b>Follow-up</b> · {esc(app.get('company') or '')} — {esc(clip(app.get('title') or '', 80))}\n"
                f"Applied on {local(applied).strftime('%d.%m') if applied else '?'}"
                + (f" ({days} days ago)" if days is not None else "") + ", no reply yet.\n"
                + ("A short, polite note to the recruiter noticeably raises the chance that you get noticed."
                   if n == 0 else "You can remind them once more — short and to the point."))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Draft the e-mail", callback_data=f"fd:{tid}"),
             InlineKeyboardButton("✉️ I wrote", callback_data=f"fw:{tid}")],
            [InlineKeyboardButton("⏰ In 3 days", callback_data=f"fs:{tid}"),
             InlineKeyboardButton("❌ Rejected", callback_data=f"fx:{tid}"),
             InlineKeyboardButton("🔕 Stop reminding", callback_data=f"fn:{tid}")],
        ] + ([[InlineKeyboardButton("🔗 Job ad", url=app["url"])]] if app.get("url", "").startswith("http") else []))
        await self.notifier.text(text, kb=kb)
        # don't re-send the same reminder tomorrow unless snoozed/answered
        self.db.upsert_application(tid, followup_due=iso(now_utc() + timedelta(days=3)))

    async def on_followup_button(self, q, action: str, tid: str) -> None:
        app = self.db.get_application(tid)
        if not app:
            await q.answer("Application not found in the tracker")
            return
        if action == "fd":
            await q.answer("Writing a draft…")
            draft = await self.ai.followup_draft(app)
            contact = app.get("contact") or ""
            await self.notifier.text(
                "✍️ <b>Follow-up draft</b>" + (f" (to: {esc(contact)})" if contact else "") +
                "\nTap the text to copy it:\n<pre>" + esc(draft) + "</pre>")
            return
        if action == "fw":
            await self.tracker.followup_sent(tid)
            await q.answer("Noted. If they don't reply, I'll remind you again in 7 days.")
        elif action == "fs":
            await self.tracker.snooze(tid, 3)
            await q.answer("I'll remind you in 3 days")
        elif action == "fn":
            self.db.upsert_application(tid, followup_due=None, followup_count=99)
            await q.answer("No more reminders for this job")
        elif action == "fx":
            await self.tracker.set_status(tid, "rejected", note="Marked in the bot")
            await q.answer("Marked as rejected")
        elif action == "fo":
            await self.tracker.set_status(tid, "no_response")
            await q.answer("Marked as no response")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass

    async def on_calendar(self, q, msg_prefix: str) -> None:
        row = self.db.one("SELECT * FROM emails WHERE msg_id LIKE ?", (msg_prefix + "%",))
        if not row:
            await q.answer("E-mail not found")
            return
        import json as _json
        c = _json.loads(row["analysis"] or "{}")
        res = await self.sheets.call("create_event", {
            "title": f"🎤 {c.get('company','')}: {c.get('position','') or KIND_TITLE.get(c.get('kind',''), 'Interview')}",
            "start": c.get("event_start"), "minutes": c.get("event_minutes") or 45,
            "location": c.get("event_location") or "", "description": (c.get("summary") or c.get("summary_ru") or "") + "\n" + (c.get("link") or ""),
        })
        await q.answer("📅 Added to Google Calendar (reminders a day and an hour before)" if res else "That didn't work")

    # ------------------------------------------------------------------ scheduled jobs
    async def job_source(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.pipeline.run_source(ctx.job.data)

    async def job_retry(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self.pipeline.retry_errors()

    async def job_mail(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.mail.sync()
        except Exception as e:  # noqa: BLE001
            log.warning("mail sync failed: %s", e)
            h = self.db.health_run("gmail", False, error=str(e))
            await self.notifier.source_health("gmail", h)
        else:
            h = self.db.health_run("gmail", True)
            await self.notifier.source_health("gmail", h)

    async def job_alerts(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Secondary LinkedIn channel: jobs from LinkedIn Job Alert e-mails (see alerts.py — no double sends)."""
        from .alerts import alert_jobs
        from .sheets import SheetsError
        since = int(self.db.kv_get("alerts_since", 0) or 0) or int(datetime.now(timezone.utc).timestamp()) - 2 * 86400
        try:
            emails = await self.sheets.call("get_job_alerts", {"since": since - 900, "max": 30}, timeout=120)
        except SheetsError as e:
            if "unknown action" in str(e):  # Apps Script not updated yet — stay quiet, try again later
                if not self.db.kv_get("alerts_waiting_logged"):
                    self.db.kv_set("alerts_waiting_logged", True)
                    log.info("LinkedIn Job Alerts: Apps Script has no get_job_alerts yet (update Code.gs)")
                return
            log.warning("job alerts failed: %s", e)
            h = self.db.health_run("linkedin_alerts", False, error=str(e))
            await self.notifier.source_health("linkedin_alerts", h)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("job alerts failed: %s", e)
            return
        fresh = [e for e in (emails or []) if not self.db.email_seen("alert:" + e["id"])]
        raws = alert_jobs(fresh)
        res = await self.pipeline.run_raws("linkedin", raws) if raws else {"new": 0, "sent": 0}
        for e in fresh:
            self.db.save_email("alert:" + e["id"], thread_id=e.get("thread_id"), received=e.get("date"),
                               sender=e.get("from"), subject=e.get("subject"), kind="job_alert")
        if emails:
            self.db.kv_set("alerts_since", max(int(x.get("epoch", 0)) for x in emails))
        if not self.db.kv_get("alerts_connected"):
            self.db.kv_set("alerts_connected", True)
            await self.notifier.text("✅ Backup channel <b>LinkedIn Job Alerts</b> connected: jobs from LinkedIn alert e-mails "
                                     "that I didn't find myself now arrive too (without duplicates).", silent=True)
        h = self.db.health_run("linkedin_alerts", True, items=len(raws), new=res["new"])
        await self.notifier.source_health("linkedin_alerts", h)
        if fresh:
            log.info("job alerts: %d e-mails, %d jobs, %d new (not found by the scraper), %d sent",
                     len(fresh), len(raws), res["new"], res["sent"])

    async def job_tracker_pull(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.tracker.pull_from_sheet()
        except Exception as e:  # noqa: BLE001
            log.warning("tracker pull failed: %s", e)

    async def job_followups(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.tracker.pull_from_sheet()
        except Exception:  # noqa: BLE001
            pass
        due = self.tracker.due_followups()
        log.info("scheduled: follow-ups — %d due", len(due))
        for app in due:
            await self.send_followup_reminder(app)
        # auto-close very old silent applications
        for app in self.db.applications({"applied", "confirmed"}):
            applied = parse_iso(app.get("applied_at"))
            if applied and (now_utc() - applied).days >= int(self.settings.get("tracker.no_response_days", 35)):
                await self.tracker.set_status(app["tracker_id"], "no_response", note="Automatically: 5 weeks without a reply")

    async def job_weekly(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.info("scheduled: weekly report")
        await self.notifier.text("🗓 " + self.report_text(7))

    async def check_boards(self) -> None:
        """Company career sites are polled in bulk, so one dead site never raised an alarm: its jobs just stopped
        arriving. Tell the user once when a site has failed for hours in a row (and again when it works)."""
        need = int(self.settings.get("health.board_alert_fails", 30))  # ≈ 3 h at the 6-min polling interval
        bad = {f"{b['kind']}:{b['key']}": b for b in self.db.failing_boards(need)}
        known = set(self.db.kv_get("boards_alerted", []) or [])
        fresh = [k for k in bad if k not in known]
        if fresh:
            lines = [f"• <b>{esc(bad[k].get('company') or k)}</b> ({esc(k)}): "
                     f"<code>{esc(clip(bad[k].get('last_error') or '', 110))}</code>" for k in fresh[:12]]
            more = f"\n…and {len(fresh) - 12} more" if len(fresh) > 12 else ""
            await self.text("⚠️ These company career sites have not answered for hours — their new jobs are not "
                            "reaching you:\n" + "\n".join(lines) + more +
                            "\nI keep trying. If the address changed, fix it in config.yaml (sources.boards) or tell Claude.")
        back = sorted(known - set(bad))
        if back and known:
            await self.text("✅ Company sites working again: " + ", ".join(esc(k) for k in back[:15]), silent=True)
        if set(bad) != known:
            self.db.kv_set("boards_alerted", sorted(bad))

    async def job_health(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Every 15 min. (1) A source whose run never finishes (hung request) updates no health row, so the
        normal 'source failed N times' alert never fires — detect it by the age of its last finished run.
        (2) Claude API spend vs the monthly limit set in the Anthropic console (when reached, scoring stops)."""
        now = now_utc()
        health = {h["name"]: h for h in self.db.health_all()}
        for name, src in self.sources.items():
            last = parse_iso((health.get(name) or {}).get("last_run")) or self.started
            last = max(last, self.started)
            limit = max(4 * int(src.interval), 2700)
            key = f"stale_alerted:{name}"
            label = SOURCE_NAMES.get(name, name)
            if (now - last).total_seconds() > limit:
                if not self.db.kv_get(key):
                    self.db.kv_set(key, True)
                    mins = int((now - last).total_seconds() // 60)
                    log.warning("source %s has not finished a run for %d min", name, mins)
                    await self.notifier.text(f"⚠️ Source <b>{esc(label)}</b> has not finished a run for {mins} min "
                                             f"(looks stuck). If it doesn't recover by itself, the watchdog will restart the service.")
            elif self.db.kv_get(key):
                self.db.kv_set(key, False)
                await self.notifier.text(f"✅ Source <b>{esc(label)}</b> is running again.", silent=True)
            # answers "OK" but returns nothing for 8+ hours → probably broken (HTML changed, key exhausted)
            z = self.db.kv_get(f"zero:{name}")
            zkey = f"zero_alerted:{name}"
            zsince = parse_iso((z or {}).get("since"))
            if z and zsince and int(z.get("n", 0)) >= 6 and (now - zsince).total_seconds() > max(8 * 3600, 6 * int(src.interval)):
                if not self.db.kv_get(zkey):
                    self.db.kv_set(zkey, True)
                    hours = int((now - zsince).total_seconds() // 3600)
                    await self.notifier.text(f"⚠️ Source <b>{esc(label)}</b> answers but has returned no jobs for {hours} h "
                                             f"({z['n']} runs in a row). The site probably changed its layout "
                                             f"or an API key ran out — time to check it.")
            elif not z and self.db.kv_get(zkey):
                self.db.kv_set(zkey, False)
                await self.notifier.text(f"✅ Source <b>{esc(label)}</b> returns jobs again.", silent=True)
        await self.check_boards()
        budget = float(self.settings.get("ai.monthly_budget_usd", 0) or 0)
        if budget > 0:
            loc = local(now)
            month = loc.strftime("%Y-%m-01")
            spent = float(self.db.usage_since(month)["cost"])
            days_in_month = calendar.monthrange(loc.year, loc.month)[1]
            elapsed = max((loc.day - 1) + loc.hour / 24, 0.5)
            projected = spent / elapsed * days_in_month
            level = 2 if spent >= 0.9 * budget else 1 if (spent >= 0.7 * budget or (loc.day > 5 and projected > budget)) else 0
            key = f"budget_alert:{month}"
            if level > int(self.db.kv_get(key, 0) or 0):
                self.db.kv_set(key, level)
                log.warning("Claude API budget: spent $%.2f of $%.0f, projected $%.2f", spent, budget, projected)
                await self.notifier.text(
                    f"💸 Claude API: ≈ ${spent:.2f} spent this month of a ${budget:.0f} limit, forecast for the month "
                    f"≈ ${projected:.2f}. When the console limit is reached, job scoring stops — "
                    f"raise the spend limit at platform.claude.com (Settings → Limits / Billing) and update ai.monthly_budget_usd in config.yaml.")

    async def job_evening(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.info("scheduled: evening nudge")
        since = now_utc() - timedelta(hours=24)
        rows = self.db.query(
            "SELECT * FROM jobs WHERE sent_at>=? AND score>=? AND (user_status IS NULL OR user_status='') "
            "ORDER BY score DESC LIMIT 6", (iso(since), int(self.settings.get("nudge.min_score", 75))))
        if not rows:
            return
        lines = [f"🌙 <b>{len(rows)} strong jobs from the last day are waiting for your reaction</b> (the earlier you apply, the better):"]
        for r in rows:
            j = self.db.row_to_job(r)
            a = j.get("analysis") or {}
            url = j.get("apply_url") or j.get("url")
            lines.append(f'• {j["score"]} · <a href="{esc(url)}">{esc(clip(a.get("title_clean") or j["title"], 60))}</a>'
                         f' — {esc(clip(a.get("company_clean") or j["company"], 30))}')
        await self.notifier.text("\n".join(lines))

    async def job_backup(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        bdir = self.settings.data_dir / "backups"
        bdir.mkdir(exist_ok=True)
        dest = bdir / f"jobradar-{now_utc():%Y%m%d}.sqlite3"
        pruned = await asyncio.to_thread(self.db.prune, int(self.settings.get("general.keep_text_days", 60)))
        if local(now_utc()).day == 1:
            await asyncio.to_thread(self.db.vacuum)
        await asyncio.to_thread(self.db.backup, dest)
        for old in sorted(bdir.glob("jobradar-*.sqlite3"))[:-7]:
            old.unlink(missing_ok=True)
        log.info("scheduled: backup %s (%d old texts pruned)", dest.name, pruned)

    # ------------------------------------------------------------------ lifecycle
    async def post_init(self, app: Application) -> None:
        self.notifier.app = app
        self.seed_boards()
        await app.bot.set_my_commands([
            BotCommand("status", "Radar status"),
            BotCommand("stats", "Statistics for 7 days"),
            BotCommand("tracker", "Application tracker (Google Sheet)"),
            BotCommand("followups", "Follow-ups due"),
            BotCommand("minscore", "Score threshold"),
            BotCommand("prefs", "Wishes for the AI"),
            BotCommand("backfill", "Send jobs that are already open"),
            BotCommand("more", "More jobs from the batch"),
            BotCommand("addcompany", "Watch a company"),
            BotCommand("sources", "Company sites"),
            BotCommand("pause", "Pause"),
            BotCommand("resume", "Resume"),
            BotCommand("help", "Help"),
        ])
        if self.sheets.enabled:
            res = await self.sheets.safe("ping")
            if res and res.get("sheet_url"):
                self.db.kv_set("sheet_url", res["sheet_url"])
        jq = app.job_queue
        for i, (name, src) in enumerate(self.sources.items()):
            jq.run_repeating(self.job_source, interval=src.interval, first=5 + i * 7 + random.uniform(0, 3),
                             data=name, name=f"source:{name}")
        # every 5 min: a LinkedIn job caught during a throttle pause is opened as soon as the pause ends
        jq.run_repeating(self.job_retry, interval=300, first=120, name="retry")
        jq.run_repeating(self.job_health, interval=900, first=1200, name="health")
        if self.sheets.enabled:
            jq.run_repeating(self.job_mail, interval=int(self.settings.get("mail.interval_sec", 300)), first=40,
                             name="mail")
            jq.run_repeating(self.job_tracker_pull, interval=1200, first=90, name="tracker_pull")
            if "linkedin" in self.sources and self.settings.get("sources.linkedin.job_alerts", True):
                jq.run_repeating(self.job_alerts, interval=300, first=150, name="linkedin_alerts")
        tz = ZoneInfo(self.settings.tz)
        jq.run_daily(self.job_followups, time=dtime(10, 0, tzinfo=tz), name="followups")
        jq.run_daily(self.job_weekly, time=dtime(19, 0, tzinfo=tz), days=(0,), name="weekly")  # 0 = Sunday in PTB
        if self.settings.get("nudge.enabled", True):
            jq.run_daily(self.job_evening, time=dtime(20, 30, tzinfo=tz), name="evening")
        jq.run_daily(self.job_backup, time=dtime(4, 0, tzinfo=tz), name="backup")
        if self.db.kv_get("last_version") != __version__:
            self.db.kv_set("last_version", __version__)
            await self.notifier.text(f"🟢 Job Radar v{__version__} started. Sources: "
                                     + ", ".join(SOURCE_NAMES.get(n, n) for n in self.sources) + ".", silent=True)

    def build(self) -> Application:
        app = (
            Application.builder()
            .token(self.settings.telegram_token)
            .post_init(self.post_init)
            .concurrent_updates(True)
            .build()
        )
        for cmd, fn in [("start", self.cmd_start), ("help", self.cmd_help), ("status", self.cmd_status),
                        ("stats", self.cmd_stats), ("pause", self.cmd_pause), ("resume", self.cmd_resume),
                        ("minscore", self.cmd_minscore), ("prefs", self.cmd_prefs), ("tracker", self.cmd_tracker),
                        ("sources", self.cmd_sources), ("addcompany", self.cmd_addcompany),
                        ("backfill", self.cmd_backfill), ("more", self.cmd_more), ("followups", self.cmd_followups),
                        ("test", self.cmd_test)]:
            app.add_handler(CommandHandler(cmd, fn))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(TypeHandler(Update, self.on_any_update), group=-1)
        app.add_error_handler(self.on_error)
        return app

    async def on_any_update(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        what = (update.message.text if update.message and update.message.text else
                f"button:{update.callback_query.data}" if update.callback_query else type(update).__name__)
        log.info("update from chat %s: %s", chat.id if chat else "?", (what or "")[:60])

    async def on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(ctx.error, (NetworkError, TimedOut)):  # Telegram "Bad Gateway" etc. — transient, retried
            log.warning("telegram network error (retrying): %s", ctx.error)
            return
        log.error("telegram error: %s", ctx.error, exc_info=ctx.error)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, str(settings.get("general.log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "telegram.ext._application", "apscheduler", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    if not settings.anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY is missing")
    radar = JobRadar(settings)
    app = radar.build()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
