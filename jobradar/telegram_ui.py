"""Rendering of Telegram messages and inline keyboards."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import ai as _ai
from .links import classify_link, google_search_url, referral_links
from .sources.boards import board_label
from .util import clip, esc, host_of, human_age, local, parse_iso

GERMAN = {
    "not_needed": "German not needed",
    "plus": "German is a plus",
    "basic_b1": "German B1 is fine",
    "b2": "⚠️ German B2 required",
    "c1_fluent": "⛔ fluent German",
    "unclear": "German: not stated",
}
WORKPLACE = {"onsite": "on-site", "hybrid": "hybrid", "remote": "remote", "unclear": ""}
ROLE = {
    "internship": "internship",
    "trainee": "trainee",
    "working_student": "working student",
    "part_time": "part-time",
    "junior": "junior",
    "full_time": "full-time",
    "freelance": "freelance",
    "other": "",
}
SKIP_REASONS = [
    ("de", "🗣 Language"),
    ("hours", "⏱ Hours"),
    ("level", "🎓 Level"),
    ("field", "🧭 Not my field"),
    ("loc", "📍 Location"),
    ("pay", "💶 Pay"),
    ("company", "🏢 Company"),
    ("other", "🤷 Other"),
]
SKIP_REASON_TEXT = dict(SKIP_REASONS)
SITE_LABELS: dict[str, str] = {}  # filled from config (sources.sites) at start-up


def score_badge(score: int | None) -> str:
    s = score or 0
    if s >= 80:
        return f"🔥 {s}"
    if s >= 65:
        return f"🟢 {s}"
    if s >= 50:
        return f"🟡 {s}"
    return f"⚪ {s}"


def source_label(job: dict) -> str:
    src = job.get("source", "")
    fam = src.split(":", 1)[0]
    meta = job.get("meta") or {}
    if fam == "linkedin":
        if meta.get("easy_apply"):
            return "LinkedIn (Easy Apply)"
        return "LinkedIn"
    if fam == "indeed":
        return "Indeed"
    if fam == "karriere":
        return "karriere.at"
    if fam == "zbp":
        return "WU ZBP Career Center"
    if fam == "jobsat":
        return "jobs.at"
    if fam == "willhaben":
        return "willhaben Jobs"
    if fam == "derstandard":
        return "DER STANDARD Jobs"
    if fam == "jooble":
        return "Jooble"
    if fam == "careerjet":
        return "Careerjet"
    if fam in SITE_LABELS:
        return SITE_LABELS[fam]
    if ":" not in src:
        return src
    return f"company site ({board_label(fam)})"


def render_job(job: dict, status_line: str = "") -> str:
    a = job.get("analysis") or {}
    meta = job.get("meta") or {}
    title = a.get("title_clean") or job.get("title") or ""
    company = a.get("company_clean") or job.get("company") or ""
    lines = []
    if status_line:
        lines.append(status_line)
    lines.append(f"{score_badge(job.get('score'))} · <b>{esc(clip(title, 120))}</b>")

    loc = a.get("location") or job.get("location") or ""
    wp = WORKPLACE.get(a.get("workplace", ""), "")
    row = [f"🏢 <b>{esc(clip(company, 60))}</b>"]
    if loc:
        row.append(f"📍 {esc(clip(loc, 40))}")
    if wp:
        row.append(wp)
    lines.append(" · ".join(row))

    facts = []
    role = ROLE.get(a.get("role_type", ""), "")
    if role:
        facts.append(role)
    if a.get("hours"):
        facts.append(f"⏱ {esc(clip(a['hours'], 40))}")
    if a.get("salary_20h_eur"):
        facts.append(f"💶 ≈€{int(round(a['salary_20h_eur'])):,}/month for {_ai.TARGET_H} h")
    elif a.get("salary"):
        facts.append(f"💶 {esc(clip(a['salary'], 45))}")
    if facts:
        lines.append(" · ".join(facts))

    facts2 = []
    lang = {"en": "🇬🇧 ad in English", "de": "🇩🇪 ad in German", "other": ""}.get(a.get("ad_language", ""), "")
    if lang:
        facts2.append(lang)
    if a.get("german"):
        facts2.append(GERMAN.get(a["german"], ""))
    if a.get("start"):
        facts2.append(f"🗓 start: {esc(clip(a['start'], 30))}")
    if a.get("deadline"):
        facts2.append(f"⏳ until {esc(clip(a['deadline'], 25))}")
    facts2 = [f for f in facts2 if f]
    if facts2:
        lines.append(" · ".join(facts2))

    if a.get("summary"):
        lines.append(f"\n<i>{esc(clip(a['summary'], 260))}</i>")

    why = [w for w in (a.get("why") or []) if w][:3]
    if why:
        lines.append("\n✅ <b>Why it fits</b>")
        lines += [f"• {esc(clip(w, 220))}" for w in why]
    risks = [r for r in (a.get("risks") or []) if r][:3]
    if risks:
        lines.append("⚠️ <b>Risks</b>")
        lines += [f"• {esc(clip(r, 200))}" for r in risks]
    missing = [m for m in (a.get("missing") or []) if m][:5]
    if missing:
        lines.append(f"❔ <b>Not stated:</b> {esc(clip(', '.join(missing), 220))} — ask when applying")
    if a.get("how_to_apply"):
        lines.append(f"📨 <b>How to apply:</b> {esc(clip(a['how_to_apply'], 200))}")
    if a.get("tip"):
        lines.append(f"💡 <i>{esc(clip(a['tip'], 220))}</i>")

    posted = parse_iso(job.get("posted_at") or "")
    tail = [f"🔗 {esc(source_label(job))}"]
    if posted:
        tail.append(f"posted {human_age(posted)}")
    if meta.get("applicants"):
        tail.append(esc(clip(meta["applicants"], 40)))
    lines.append("\n" + " · ".join(tail))
    text = "\n".join(lines)
    return text[:4000]


def apply_button_label(url: str, job: dict) -> str:
    kind = classify_link(url)
    if kind == "company":
        return "🚀 Apply on the company site"
    if kind == "linkedin":
        return "🚀 Apply on LinkedIn" + (" (Easy Apply)" if (job.get("meta") or {}).get("easy_apply") else "")
    return f"🚀 Apply ({host_of(url)})"


def job_keyboard(settings, job: dict, state: str = "new") -> InlineKeyboardMarkup:
    jid = job["id"]
    apply_url = job.get("apply_url") or job.get("url")
    rows: list[list[InlineKeyboardButton]] = []
    if apply_url:
        rows.append([InlineKeyboardButton(apply_button_label(apply_url, job), url=apply_url)])
    extra = []
    if classify_link(apply_url or "") != "company":
        extra.append(InlineKeyboardButton("🔎 Find on the company site", url=google_search_url(job.get("company", ""), job.get("title", ""))))
    if job.get("url") and job.get("url") != apply_url:
        extra.append(InlineKeyboardButton("📄 Original ad", url=job["url"]))
    if extra:
        rows.append(extra)
    refs = referral_links(settings, job.get("company", ""), job.get("company_linkedin", ""))
    rows.append([InlineKeyboardButton(t, url=u) for t, u in refs[:3]])
    if state == "new":
        rows.append([
            InlineKeyboardButton("✅ Applied", callback_data=f"ap:{jid}"),
            InlineKeyboardButton("⭐ Save", callback_data=f"sv:{jid}"),
            InlineKeyboardButton("🙅 Not interested", callback_data=f"sk:{jid}"),
        ])
    elif state == "saved":
        rows.append([
            InlineKeyboardButton("✅ Applied", callback_data=f"ap:{jid}"),
            InlineKeyboardButton("↩️ Remove from saved", callback_data=f"un:{jid}"),
        ])
    elif state == "applied":
        rows.append([InlineKeyboardButton("↩️ Undo “Applied”", callback_data=f"un:{jid}")])
    rows.append([InlineKeyboardButton(f"📋 For Claude: {docs_label(job)}", callback_data=f"cl:{jid}")])
    return InlineKeyboardMarkup(rows)


def skip_reasons_keyboard(jid: int) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(label, callback_data=f"sr:{jid}:{code}") for code, label in SKIP_REASONS]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([InlineKeyboardButton("↩️ Back", callback_data=f"bk:{jid}")])
    return InlineKeyboardMarkup(rows)


def skipped_text(job: dict, reason_code: str) -> str:
    a = job.get("analysis") or {}
    title = a.get("title_clean") or job.get("title") or ""
    company = a.get("company_clean") or job.get("company") or ""
    reason = SKIP_REASON_TEXT.get(reason_code, "")
    return f"🙅 <s>{esc(clip(title, 90))}</s> · {esc(clip(company, 40))}" + (f" — {esc(reason)}" if reason else "")


def skipped_keyboard(jid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Restore", callback_data=f"un:{jid}")]])


def status_line(kind: str, when=None) -> str:
    t = local(when).strftime("%d.%m %H:%M") if when else ""
    return {
        "applied": f"✅ <b>Applied</b> {t}",
        "saved": f"⭐ <b>Saved</b> {t}",
    }.get(kind, "")


DOC_NAMES = {
    "short_motivation": "short motivation text",
    "transcript": "transcript of records",
    "certificates": "diplomas / certificates",
    "references": "references / Arbeitszeugnisse",
    "photo": "photo",
    "portfolio": "portfolio",
    "online_form": "online form",
    "video": "video",
    "test_case": "test / case study",
}


def docs_label(job: dict) -> str:
    a = job.get("analysis") or {}
    cl = a.get("cover_letter") or "not_mentioned"
    short = "short_motivation" in (a.get("apply_docs") or [])
    if cl == "not_wanted":
        return "CV + short text" if short else "CV"
    if cl == "not_mentioned" and short:
        return "CV + short text"
    return "CV + letter"


def docs_request(job: dict) -> str:
    """What exactly Claude should prepare — follows the ad's own application instructions."""
    a = job.get("analysis") or {}
    cl = a.get("cover_letter") or "not_mentioned"
    docs = [d for d in (a.get("apply_docs") or []) if d not in ("cv", "cover_letter")]
    if cl == "not_wanted":
        what = "CV only — NO cover letter (the ad says so)"
        if "short_motivation" in docs:
            what = "CV + the short text the ad asks for instead of a letter — NO cover letter (the ad says so)"
    elif cl == "required":
        what = "CV + cover letter (the ad requires a letter)"
    elif cl == "optional":
        what = "CV + a short cover letter (the ad says a letter is optional)"
    elif "short_motivation" in docs:
        what = "CV + the short motivation text the ad asks for (no regular cover letter)"
    else:
        what = "CV + cover letter (the ad does not say whether a letter is needed — make one to be safe)"
    extra = [DOC_NAMES[d] for d in docs if d in DOC_NAMES and d != "short_motivation"]
    out = (f"Prepare for this job: {what}. Follow the project instructions (check the fit first and ask "
           "questions if needed); the format and length of every document exactly as the ad asks.")
    if extra:
        out += " They also ask for: " + ", ".join(extra) + " — remind me if anything is missing."
    return out


def claude_export(job: dict, earlier: list[dict] | None = None) -> str:
    a = job.get("analysis") or {}
    lines = [
        f"# {a.get('title_clean') or job.get('title')} — {a.get('company_clean') or job.get('company')}",
        "",
        docs_request(job),
        "",
        f"- Company: {job.get('company')}",
        f"- Location: {job.get('location') or a.get('location') or 'not stated'}",
        f"- Apply link: {job.get('apply_url') or job.get('url')}",
        f"- Source: {source_label(job)} — {job.get('url')}",
        f"- Radar score: {job.get('score')}/100",
    ]
    if a.get("hours"):
        lines.append(f"- Hours: {a['hours']}")
    if a.get("salary"):
        lines.append(f"- Salary: {a['salary']}")
    if a.get("deadline"):
        lines.append(f"- Deadline: {a['deadline']}")
    if a.get("how_to_apply"):
        lines.append(f"- How to apply (from the ad): {a['how_to_apply']}")
    if earlier:
        lines.append("- ⚠️ The tracker already has applications to this company: " + "; ".join(
            f"{e.get('title') or '?'} — {e.get('status') or '?'}" + (f" ({str(e.get('applied_at'))[:10]})"
                                                                    if e.get("applied_at") else "")
            for e in earlier[:5]) + " — do not contradict them in the documents.")
    if a.get("why"):
        lines += ["", "## Why it fits (radar's assessment)"] + [f"- {w}" for w in a["why"]]
    if a.get("risks"):
        lines += ["", "## Risks"] + [f"- {r}" for r in a["risks"]]
    if a.get("missing"):
        lines += ["", "## Not stated in the ad"] + [f"- {m}" for m in a["missing"]]
    if a.get("tip"):
        lines += ["", f"Tip: {a['tip']}"]
    lines += ["", "## Job description", "", job.get("description") or "(description not available — open the link)"]
    return "\n".join(lines)
