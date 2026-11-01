"""Rendering of Telegram messages and inline keyboards."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import ai as _ai
from .links import classify_link, google_search_url, referral_links
from .sources.boards import board_label
from .util import clip, esc, host_of, human_age, local, parse_iso

GERMAN = {
    "not_needed": "немецкий не нужен",
    "plus": "немецкий — плюс",
    "basic_b1": "немецкий B1 ок",
    "b2": "⚠️ нужен немецкий B2",
    "c1_fluent": "⛔ свободный немецкий",
    "unclear": "немецкий: не указан",
}
WORKPLACE = {"onsite": "офис", "hybrid": "гибрид", "remote": "удалённо", "unclear": ""}
ROLE = {
    "internship": "стажировка",
    "trainee": "трейни",
    "working_student": "working student",
    "part_time": "частичная занятость",
    "junior": "junior",
    "full_time": "полная ставка",
    "freelance": "фриланс",
    "other": "",
}
SKIP_REASONS = [
    ("de", "🇩🇪 Немецкий"),
    ("hours", "⏱ Часы"),
    ("level", "🎓 Уровень"),
    ("field", "🧭 Не моё"),
    ("loc", "📍 Место"),
    ("pay", "💶 Деньги"),
    ("company", "🏢 Компания"),
    ("other", "🤷 Другое"),
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
    return f"сайт компании ({board_label(fam)})"


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
        facts.append(f"💶 ≈€{int(round(a['salary_20h_eur'])):,}/мес за {_ai.TARGET_H} ч".replace(",", " "))
    elif a.get("salary"):
        facts.append(f"💶 {esc(clip(a['salary'], 45))}")
    if facts:
        lines.append(" · ".join(facts))

    facts2 = []
    lang = {"en": "🇬🇧 объявление на англ.", "de": "🇦🇹 объявление на нем.", "other": ""}.get(a.get("ad_language", ""), "")
    if lang:
        facts2.append(lang)
    if a.get("german"):
        facts2.append(GERMAN.get(a["german"], ""))
    if a.get("start"):
        facts2.append(f"🗓 старт: {esc(clip(a['start'], 30))}")
    if a.get("deadline"):
        facts2.append(f"⏳ до {esc(clip(a['deadline'], 25))}")
    facts2 = [f for f in facts2 if f]
    if facts2:
        lines.append(" · ".join(facts2))

    if a.get("summary"):
        lines.append(f"\n<i>{esc(clip(a['summary'], 260))}</i>")

    why = [w for w in (a.get("why") or []) if w][:3]
    if why:
        lines.append("\n✅ <b>Почему подходит</b>")
        lines += [f"• {esc(clip(w, 220))}" for w in why]
    risks = [r for r in (a.get("risks") or []) if r][:3]
    if risks:
        lines.append("⚠️ <b>Риски</b>")
        lines += [f"• {esc(clip(r, 200))}" for r in risks]
    missing = [m for m in (a.get("missing") or []) if m][:5]
    if missing:
        lines.append(f"❔ <b>Не указано:</b> {esc(clip(', '.join(missing), 220))} — уточни при отклике")
    if a.get("how_to_apply"):
        lines.append(f"📨 <b>Как подаваться:</b> {esc(clip(a['how_to_apply'], 200))}")
    if a.get("tip"):
        lines.append(f"💡 <i>{esc(clip(a['tip'], 220))}</i>")

    posted = parse_iso(job.get("posted_at") or "")
    tail = [f"🔗 {esc(source_label(job))}"]
    if posted:
        tail.append(f"опубл. {human_age(posted)}")
    if meta.get("applicants"):
        tail.append(esc(clip(meta["applicants"], 40)))
    lines.append("\n" + " · ".join(tail))
    text = "\n".join(lines)
    return text[:4000]


def apply_button_label(url: str, job: dict) -> str:
    kind = classify_link(url)
    if kind == "company":
        return "🚀 Откликнуться на сайте компании"
    if kind == "linkedin":
        return "🚀 Откликнуться в LinkedIn" + (" (Easy Apply)" if (job.get("meta") or {}).get("easy_apply") else "")
    return f"🚀 Откликнуться ({host_of(url)})"


def job_keyboard(settings, job: dict, state: str = "new") -> InlineKeyboardMarkup:
    jid = job["id"]
    apply_url = job.get("apply_url") or job.get("url")
    rows: list[list[InlineKeyboardButton]] = []
    if apply_url:
        rows.append([InlineKeyboardButton(apply_button_label(apply_url, job), url=apply_url)])
    extra = []
    if classify_link(apply_url or "") != "company":
        extra.append(InlineKeyboardButton("🔎 Найти на сайте компании", url=google_search_url(job.get("company", ""), job.get("title", ""))))
    if job.get("url") and job.get("url") != apply_url:
        extra.append(InlineKeyboardButton("📄 Оригинал объявления", url=job["url"]))
    if extra:
        rows.append(extra)
    refs = referral_links(settings, job.get("company", ""), job.get("company_linkedin", ""))
    rows.append([InlineKeyboardButton(t, url=u) for t, u in refs[:3]])
    if state == "new":
        rows.append([
            InlineKeyboardButton("✅ Подался", callback_data=f"ap:{jid}"),
            InlineKeyboardButton("⭐ Сохранить", callback_data=f"sv:{jid}"),
            InlineKeyboardButton("🙅 Не интересно", callback_data=f"sk:{jid}"),
        ])
    elif state == "saved":
        rows.append([
            InlineKeyboardButton("✅ Подался", callback_data=f"ap:{jid}"),
            InlineKeyboardButton("↩️ Убрать из сохранённых", callback_data=f"un:{jid}"),
        ])
    elif state == "applied":
        rows.append([InlineKeyboardButton("↩️ Отменить «Подался»", callback_data=f"un:{jid}")])
    rows.append([InlineKeyboardButton(f"📋 Для Claude: {docs_label(job)}", callback_data=f"cl:{jid}")])
    return InlineKeyboardMarkup(rows)


def skip_reasons_keyboard(jid: int) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(label, callback_data=f"sr:{jid}:{code}") for code, label in SKIP_REASONS]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([InlineKeyboardButton("↩️ Назад", callback_data=f"bk:{jid}")])
    return InlineKeyboardMarkup(rows)


def skipped_text(job: dict, reason_code: str) -> str:
    a = job.get("analysis") or {}
    title = a.get("title_clean") or job.get("title") or ""
    company = a.get("company_clean") or job.get("company") or ""
    reason = SKIP_REASON_TEXT.get(reason_code, "")
    return f"🙅 <s>{esc(clip(title, 90))}</s> · {esc(clip(company, 40))}" + (f" — {esc(reason)}" if reason else "")


def skipped_keyboard(jid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Вернуть", callback_data=f"un:{jid}")]])


def status_line(kind: str, when=None) -> str:
    t = local(when).strftime("%d.%m %H:%M") if when else ""
    return {
        "applied": f"✅ <b>Подался</b> {t}",
        "saved": f"⭐ <b>Сохранено</b> {t}",
    }.get(kind, "")


DOC_NAMES = {
    "short_motivation": "короткий мотивационный текст",
    "transcript": "выписка оценок (transcript)",
    "certificates": "дипломы/сертификаты",
    "references": "рекомендации / Arbeitszeugnisse",
    "photo": "фото",
    "portfolio": "портфолио",
    "online_form": "онлайн-анкета",
    "video": "видео",
    "test_case": "тест/кейс",
}


def docs_label(job: dict) -> str:
    a = job.get("analysis") or {}
    cl = a.get("cover_letter") or "not_mentioned"
    short = "short_motivation" in (a.get("apply_docs") or [])
    if cl == "not_wanted":
        return "CV + короткий текст" if short else "CV"
    if cl == "not_mentioned" and short:
        return "CV + короткий текст"
    return "CV + письмо"


def docs_request(job: dict) -> str:
    """What exactly Claude should prepare — follows the ad's own application instructions."""
    a = job.get("analysis") or {}
    cl = a.get("cover_letter") or "not_mentioned"
    docs = [d for d in (a.get("apply_docs") or []) if d not in ("cv", "cover_letter")]
    if cl == "not_wanted":
        what = "только CV — cover letter НЕ нужен (так сказано в объявлении)"
        if "short_motivation" in docs:
            what = "CV + короткий текст, который просят вместо письма — cover letter НЕ нужен (так сказано в объявлении)"
    elif cl == "required":
        what = "CV + cover letter (письмо обязательно по объявлению)"
    elif cl == "optional":
        what = "CV + короткий cover letter (по объявлению письмо по желанию)"
    elif "short_motivation" in docs:
        what = "CV + короткий мотивационный текст, который просят в объявлении (обычный cover letter не нужен)"
    else:
        what = "CV + cover letter (в объявлении не сказано, нужно ли письмо — сделай на всякий случай)"
    extra = [DOC_NAMES[d] for d in docs if d in DOC_NAMES and d != "short_motivation"]
    out = (f"Подготовь под эту вакансию: {what}. Работай по инструкциям проекта (сначала проверь fit и задай "
           "вопросы, если нужно); формат и объём каждого документа — строго как просит объявление.")
    if extra:
        out += " Ещё просят приложить: " + ", ".join(extra) + " — напомни мне, если чего-то не хватает."
    return out


def claude_export(job: dict, earlier: list[dict] | None = None) -> str:
    a = job.get("analysis") or {}
    lines = [
        f"# {a.get('title_clean') or job.get('title')} — {a.get('company_clean') or job.get('company')}",
        "",
        docs_request(job),
        "",
        f"- Компания: {job.get('company')}",
        f"- Локация: {job.get('location') or a.get('location') or 'не указано'}",
        f"- Ссылка для отклика: {job.get('apply_url') or job.get('url')}",
        f"- Источник: {source_label(job)} — {job.get('url')}",
        f"- Оценка радара: {job.get('score')}/100",
    ]
    if a.get("hours"):
        lines.append(f"- Часы: {a['hours']}")
    if a.get("salary"):
        lines.append(f"- Зарплата: {a['salary']}")
    if a.get("deadline"):
        lines.append(f"- Дедлайн: {a['deadline']}")
    if a.get("how_to_apply"):
        lines.append(f"- Как подаваться (из объявления): {a['how_to_apply']}")
    if earlier:
        lines.append("- ⚠️ В трекере уже есть отклики в эту компанию: " + "; ".join(
            f"{e.get('title') or '?'} — {e.get('status') or '?'}" + (f" ({str(e.get('applied_at'))[:10]})"
                                                                    if e.get("applied_at") else "")
            for e in earlier[:5]) + " — не противоречь им в документах.")
    if a.get("why"):
        lines += ["", "## Почему подходит (оценка радара)"] + [f"- {w}" for w in a["why"]]
    if a.get("risks"):
        lines += ["", "## Риски"] + [f"- {r}" for r in a["risks"]]
    if a.get("missing"):
        lines += ["", "## Не указано в объявлении"] + [f"- {m}" for m in a["missing"]]
    if a.get("tip"):
        lines += ["", f"Совет: {a['tip']}"]
    lines += ["", "## Текст вакансии", "", job.get("description") or "(описание не получено — открой ссылку)"]
    return "\n".join(lines)
