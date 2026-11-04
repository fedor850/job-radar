"""All Claude API calls: triage, scoring, e-mail classification, follow-up drafts, company-link search."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import anthropic

from .util import clip, detect_lang, location_ok, strict_quote, weekly_hours_range, VIENNA_RE

log = logging.getLogger("jobradar.ai")

# USD per million tokens (input, output, cache_write_1h, cache_read) — Claude API pricing, Oct 2026.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.20, 0.01),
    "claude-sonnet-5-5": (2.0, 10.0, 4.0, 0.10),
    "claude-opus-5-5": (4.0, 20.0, 8.0, 0.20),
}

BLOCKERS = ["fulltime_only", "german_required", "senior", "wrong_field", "not_vienna", "eu_only",
            "not_eligible", "expired"]

TRIAGE_CATEGORIES = ["senior_title", "unrelated_field", "apprenticeship_or_pupils", "none"]
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "obviously_irrelevant": {"type": "boolean",
                                 "description": "true ONLY if the TITLE proves the job cannot fit; default false"},
        "category": {"type": "string", "enum": TRIAGE_CATEGORIES,
                     "description": "why it is irrelevant; 'none' when obviously_irrelevant=false"},
        "evidence": {"type": "string",
                     "description": "the exact words copied from the TITLE that prove it (e.g. 'Senior', 'Koch'); "
                                    "empty when obviously_irrelevant=false"},
        "reason": {"type": "string", "description": "max 12 words"},
    },
    "required": ["obviously_irrelevant", "category", "evidence", "reason"],
    "additionalProperties": False,
}

SCORE_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "0-100 fit score"},
        "verdict": {"type": "string", "enum": ["strong", "good", "maybe", "weak", "reject"]},
        "blockers": {"type": "array", "items": {"type": "string", "enum": BLOCKERS}},
        "title_clean": {"type": "string", "description": "job title without gender tags like (m/w/d)"},
        "company_clean": {"type": "string"},
        "role_type": {"type": "string", "enum": ["internship", "trainee", "working_student", "part_time", "junior",
                                                  "full_time", "freelance", "other"]},
        "hours": {"type": "string", "description": "weekly hours as stated, e.g. '20 h/week', '20–30 h', 'not stated', 'full-time'"},
        "hours_ok": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "german": {"type": "string", "enum": ["not_needed", "plus", "basic_b1", "b2", "c1_fluent", "unclear"]},
        "ad_language": {"type": "string", "enum": ["en", "de", "other"]},
        "salary": {"type": "string", "description": "as stated in the ad, short; empty if none"},
        "salary_20h_eur": {"anyOf": [{"type": "number"}, {"type": "null"}],
                           "description": "gross EUR per month pro-rated to {{T}} h/week, null if unknown"},
        "start": {"type": "string", "description": "start date, max 25 chars, e.g. 'immediately', 'Jan 2027', 'flexible'; empty if none"},
        "deadline": {"type": "string", "description": "application deadline as a date, max 20 chars; empty if none"},
        "workplace": {"type": "string", "enum": ["onsite", "hybrid", "remote", "unclear"]},
        "location": {"type": "string", "description": "city/district, short"},
        "summary": {"type": "string", "description": "one sentence: what the job actually is"},
        "why": {"type": "array", "items": {"type": "string"}, "description": "2-3 bullets"},
        "risks": {"type": "array", "items": {"type": "string"}, "description": "0-3 bullets"},
        "tip": {"type": "string", "description": "one line: what to emphasise in CV/cover letter"},
        "german_evidence": {"type": "string",
                            "description": "EXACT quote (original language, max 140 chars) of the sentence where the ad "
                                           "states the German requirement or says English is enough / German optional / "
                                           "English is the working language; empty if the ad says nothing about German"},
        "how_to_apply": {"type": "string",
                         "description": "max 160 chars: HOW and WHAT to send exactly as the ad says (channel, "
                                        "e-mail address, language, special asks like '3 sentences about yourself'); "
                                        "empty if the ad gives no specific instructions"},
        "apply_docs": {"type": "array",
                       "items": {"type": "string", "enum": ["cv", "cover_letter", "short_motivation", "transcript",
                                                            "certificates", "references", "photo", "portfolio",
                                                            "online_form", "video", "test_case", "other"]},
                       "description": "documents/steps the ad explicitly asks for; empty if the ad does not say"},
        "cover_letter": {"type": "string", "enum": ["required", "optional", "not_wanted", "not_mentioned"],
                         "description": "cover/motivation letter per the ad: not_wanted if the ad says no cover letter "
                                        "is needed (e.g. 'no cover-letter essays', 'CV only', 'kein Anschreiben'), "
                                        "required only if the ad explicitly asks for one"},
        "missing": {"type": "array",
                    "items": {"type": "string", "enum": ["hours", "pay", "German requirement", "start date",
                                                         "work format"]},
                    "description": "criteria that the ad does NOT mention at all"},
        "evidence": {"type": "array",
                     "description": "one item per blocker: the EXACT passage of the ad (copied word for word, max 200 "
                                    "chars) that states it. A blocker without such a passage must not be listed.",
                     "items": {"type": "object",
                               "properties": {"blocker": {"type": "string", "enum": BLOCKERS},
                                              "quote": {"type": "string"}},
                               "required": ["blocker", "quote"], "additionalProperties": False}},
    },
    "required": ["score", "verdict", "blockers", "title_clean", "company_clean", "role_type", "hours", "hours_ok",
                 "german", "ad_language", "salary", "salary_20h_eur", "start", "deadline", "workplace", "location",
                 "summary", "why", "risks", "tip", "german_evidence", "how_to_apply", "apply_docs", "cover_letter",
                 "missing", "evidence"],
    "additionalProperties": False,
}

EMAIL_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "job_related": {"type": "boolean"},
        "kind": {"type": "string", "enum": ["confirmation", "rejection", "interview", "assessment", "offer",
                                            "recruiter_outreach", "info_request", "job_alert", "other"]},
        "company": {"type": "string"},
        "position": {"type": "string"},
        "tracker_match": {"type": "string", "description": "tracker_id from the provided list, or empty"},
        "summary": {"type": "string", "description": "max 2 sentences"},
        "action": {"type": "string", "description": "what {{FIRST}} must do now, empty if nothing"},
        "deadline": {"type": "string", "description": "ISO date/datetime if a deadline is stated, else empty"},
        "event_start": {"type": "string", "description": "ISO datetime ({{TZ}}) of a FIXED interview/test slot, else empty"},
        "event_minutes": {"type": "integer"},
        "event_location": {"type": "string", "description": "address or video link, else empty"},
        "contact_name": {"type": "string"},
        "contact_email": {"type": "string"},
    },
    "required": ["job_related", "kind", "company", "position", "tracker_match", "summary", "action",
                 "deadline", "event_start", "event_minutes", "event_location", "contact_name", "contact_email"],
    "additionalProperties": False,
}

TRIAGE_SYSTEM = """You are a fast pre-filter for a job-alert bot. The candidate profile is below.
Decide whether a posting is OBVIOUSLY irrelevant judging by its TITLE. Default answer: obviously_irrelevant=false.
Be PERMISSIVE: unclear fit, unknown German level, unknown hours, little text → obviously_irrelevant=false.
NEVER reject because of hours / full-time, German or any language, years of experience, location, salary or
anything else in the snippet — all of that is checked later on the FULL text with exact quotes. A guess
("likely", "probably", "seems", "implies") is never a reason.
Every reject needs category + evidence = the exact word(s) of the TITLE that prove it (copied, not paraphrased);
the radar checks the evidence against the title and ignores rejects without it.

Reject ONLY when the TITLE makes it obvious that the job cannot fit (category in brackets):
- [senior_title] clearly senior/lead/head/director title (e.g. "Senior …", "Head of …", "Team Lead", "Leiter:in",
  "Director") — but "Junior …", "Associate", "Assistant", "Trainee", "Intern", "Student" and plain "… Manager"
  titles are NOT senior (experience is checked later);
- [unrelated_field] clearly unrelated field: medicine/nursing/care, therapy, pharmacy staff, construction or technical trades,
  electricians, mechanics, kitchen/cooks/waiters/bar, cleaning, security guards, drivers/couriers, warehouse/production
  workers, hairdressers, teachers/childcare, lawyers needing a law degree (Rechtsanwaltsanwärter, Konzipient),
  pure software/hardware/DevOps engineering, IT system administration, architecture/civil engineering,
  natural-science researchers/lab staff, video/graphic design as the core craft;
- [apprenticeship_or_pupils] an apprenticeship (Lehre/Lehrling) or a job for school pupils;
Everything business-related or adjacent (sales, finance, accounting, controlling, consulting, marketing, operations,
analytics, data/BI/data-quality, product management, UX research, e-commerce, admin/office, HR/recruiting, communications/PR,
project work, customer success/support, start-up generalist roles, banking, audit/tax, compliance/AML/KYC, procurement,
supply-chain or logistics OFFICE roles, real estate, ESG/sustainability, NGO/international organisations, events,
IT/SAP/ERP consulting, tech sales, research assistant in economics/business/social sciences, university student
assistant jobs) at student/intern/junior/entry level → pass. When in doubt → pass.

Candidate profile:
"""

SCORE_SYSTEM_TEMPLATE = """You evaluate job postings for one specific candidate (profile below) and return a structured verdict.
Write all free-text fields (summary, why, risks, tip, hours, start, deadline, how_to_apply) in ENGLISH, short and concrete.

How to score (0–100):
- 85–100: student/intern/trainee/working-student/junior role in a business field that matches his experience,
  compatible with ≤{{T}} h/week, English-friendly (German not needed, a plus, or B1), {{CITY}}/hybrid/remote.
- 70–84: good fit with one minor gap (e.g. hours {{T1}}–{{M}} or flexible, adjacent field, one missing hard skill).
- 50–69: plausible but real gaps (less related field, wants a Master's student, several unknowns).
- 30–49: weak fit, several gaps.
- 0–29: not suitable or hard blocker.

Hard blockers — use ONLY when the posting EXPLICITLY says so (add to "blockers", keep score ≤ 25). For EVERY blocker
add an "evidence" item: the exact passage of the ad (title, location line or description, copied word for word,
contiguous, max 200 chars) that states it. The radar verifies each quote against the ad text and checks that it
really says what the blocker claims; a blocker without such a quote is discarded. So: no quote → no blocker —
unknowns, impressions and "likely/probably" are NEVER blockers (score the job on what the ad does say).
- fulltime_only: the ad requires MORE than {{M}} h/week — explicitly full-time (Vollzeit / full-time / 38,5–40 h) with NO
  part-time option, or a minimum above {{M}} h (e.g. "ab 30 Wochenstunden", "30–38,5 h"). Not for internships or
  working-student/part-time roles that allow ≤ {{M}} h, not when it says Teilzeit möglich / {{T}}–40 h / "ab {{T}} Stunden".
  If a full-time internship is offered without a part-time option, it IS fulltime_only.
  Hours of {{T1}}–{{M}} per week (e.g. "{{M}} h", "{{M1}} Stunden", "{{T}}–30 h") are NOT a blocker: set hours_ok="yes" and add the
  risk "⏱ more than {{T}} h — propose {{T}} h/week to the employer" (the candidate may be able to negotiate).
- german_required: German above B1 is REQUIRED, or German is the main working language. Map the wording:
  "gute Deutschkenntnisse" / "good German" / "B2" → german="b2" → BLOCKER;
  "sehr gute" / "ausgezeichnete" / "exzellente" / "perfekte" / "verhandlungssicher" / "fließend" / "Muttersprache" /
  "C1" / "C2" / "German-speaking" / "fluent German" → german="c1_fluent" → BLOCKER;
  also a BLOCKER: customer-facing work (sales, telesales, customer service, support, recruiting, reception) for the
  Austrian/German market in an ad written in German, unless it says English is the working language.
  NOT a blocker: German "von Vorteil" / "wünschenswert" / "ein Plus" / "nice to have" / "Grundkenntnisse" / basic / B1,
  and "Deutsch ODER Englisch" / "Deutsch und/oder Englisch" / "German or English" (either language is enough, whatever
  the level) → german="plus" or "basic_b1".
- senior: requires 3+ years of professional experience in that field (quote the sentence with the years / "mehrjährige"),
  or it is a lead/head role (quote the title). 1–2 years required is NOT senior — mention it in risks.
- wrong_field: clearly unrelated to business/economics (see profile "weaker fit"). Adjacent fields listed in the profile
  ("also a good fit") are NOT wrong_field — score them on their merits.
- not_vienna: on-site outside the {{CITY}} area and not remote.
- eu_only: requires EU/EEA citizenship or an existing unrestricted work permit.
- not_eligible: only for a group he is not in (law students only, school pupils, apprenticeship, Master's only, graduates only).
- expired: the application deadline has clearly passed (today's date is given; quote the deadline with its date).

German-language ads: if the DESCRIPTION is written in German (not just headings or the job board's interface) and
the ad does NOT explicitly say that German is optional / a plus / basic is enough / "Deutsch oder Englisch" / English is
the working language, the job is NOT suitable (the radar blocks it automatically) — then set german="unclear" and leave
german_evidence empty. Never assume "German not needed" for a German-language ad without such a sentence.
german_evidence must be copied word for word from the ad (it is checked against the text).

Facts must be consistent and faithful to the ad:
- "hours" = the contractual weekly hours (e.g. "50%" → "20 h/week (50%)"). Do not change them because of remarks about
  intensity. If the ad warns about intense weeks (e.g. "some weeks those 20 hours will feel like 40"), describe it
  accurately as a remark about intensity ("they warn that in busy weeks 20 h can feel like 40"), never as
  "often 40 hours" or more hours than stated.
- risks/why/tip must not contradict hours, salary, german, start or each other; never exaggerate or add "often",
  "usually", "probably" when the ad does not say so.
Application instructions: read how the ad asks to apply. Fill cover_letter ("not_wanted" if it says e.g. "no cover
letter", "kein Motivationsschreiben nötig", "CV only"), apply_docs and how_to_apply. If the ad asks for something
instead of a cover letter (e.g. "three sentences on why you", "kurzes Motivationsschreiben (max. 5 Sätze)"), use
"short_motivation". The "tip" must match these instructions (do not suggest a cover letter when it is not wanted).

Missing information is NOT a reason to reject. "missing" = criteria the ad does not mention AT ALL (weekly hours, pay,
German level, start date, on-site/remote); if a criterion is stated in any form, do not list it. Judge the job on
everything else. Penalties for unknowns are small:
- hours not stated: 0 for internships/working-student/part-time roles; −5 for regular jobs (hours_ok="unclear").
- German level not stated and the ad is in English: no penalty (German is usually not needed); do not list it in missing.
- pay not stated: no penalty, just "pay" in missing.
{{EXTRA_RULES}}Austrian ads often state salary "auf Vollzeitbasis (38,5h)" — that alone does not make a part-time/internship role full-time.
Salary: compute salary_20h_eur = monthly gross pro-rated to {{T}} h/week (e.g. €2,600 for 38.5 h → ≈ €{{EX1}};
€15/h → ≈ €{{EX2}}). If below €{{SAL}} add the risk "💶 below your minimum of €{{SAL}} for {{T}} h (≈ €X)" — this never blocks.
Never invent pay.
"why": 2–3 bullets that connect concrete requirements of THIS posting to concrete facts from his profile
(numbers welcome, e.g. "{{WHY_EXAMPLE}}"). No generic praise.
"risks": 0–3 bullets about real gaps stated in the ad (hard skill, eligibility, low pay, start date, hours {{T}}–{{M}}).
Do not repeat items from "missing" in "risks".
"tip": one line — what to emphasise in the application for THIS role.
Be honest and calibrated; do not inflate scores.
Style: natural, concise English. Keep German terms in their original spelling (e.g. "ganzjährig", "Werkstudent",
"Vollzeit"). No HTML tags, no markdown in any field. Address the candidate as "you", never by name.

Candidate profile:
"""

EMAIL_SYSTEM_TEMPLATE = """You read e-mails in a job seeker's inbox ({{NAME}}, applying for student/intern/junior jobs in {{CITY}})
and classify those related to his job applications. Kinds:
- confirmation: application received / thank you for applying / Eingangsbestätigung / LinkedIn or Indeed "application sent".
- rejection: not moving forward / Absage / leider / unfortunately.
- interview: invitation to an interview/call/meeting (or scheduling one).
- assessment: online test, case study, video interview, assignment, HireVue, Pymetrics, etc.
- offer: job offer / contract.
- recruiter_outreach: a recruiter/headhunter contacts him about a role.
- info_request: employer asks for documents/information.
- job_alert: automated job recommendations/alerts (LinkedIn/Indeed/karriere alerts) — not an application event.
- other: anything else (job_related=false for non-job e-mails).
Match the e-mail to one of his tracked applications when the company clearly matches (return its tracker_id).
Write summary and action in English. Use event_start only for a fixed, confirmed date/time, in ISO 8601 with offset.
"""


_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _clean_text(v: str) -> str:
    v = _TAG.sub(" ", v or "")
    v = v.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    v = re.sub(r"[)\]]{2,}$", "", v.strip())
    return re.sub(r"\s{2,}", " ", v).strip()


def _sanitize(res: dict) -> dict:
    for k, v in list(res.items()):
        if isinstance(v, str):
            res[k] = _clean_text(v)
        elif isinstance(v, list):
            res[k] = [_clean_text(x) if isinstance(x, str) else x for x in v]
    return res


GERMAN_OK = ("not_needed", "plus", "basic_b1")


def _validate_score(res: dict) -> None:
    """Fail-safe: a malformed answer raises → the job is retried later instead of being judged on garbage."""
    if not isinstance(res, dict):
        raise ValueError("score: not an object")
    if not isinstance(res.get("score"), (int, float)):
        raise ValueError("score: missing score")
    for k in ("blockers", "evidence", "why", "risks", "missing", "apply_docs"):
        if not isinstance(res.get(k, []), list):
            raise ValueError(f"score: {k} is not a list")
        res.setdefault(k, [])
    res["blockers"] = [b for b in res["blockers"] if b in BLOCKERS]


def german_ad_check(res: dict, description: str) -> str:
    """The rule for ads written in German (Austrian preset): OK only if the ad explicitly says German is optional/a plus/B1
    or that English is enough. Returns '' (fine) or a short reason. Conservative: applies only when BOTH the
    model and a stop-word count agree that the description is German."""
    lang, de, en = detect_lang(description)
    res["_lang"] = f"{lang} de={de} en={en}"
    if lang != "de" or res.get("ad_language") != "de":
        return ""
    if res.get("german") in ("b2", "c1_fluent"):
        return ""  # already german_required
    ev = res.get("german_evidence") or ""
    if res.get("german") in GERMAN_OK and ev and strict_quote(ev, description):
        return ""
    if res.get("german") in GERMAN_OK and ev:
        res["_german_ok_unverified"] = True  # the model says "German optional" but its quote is not in the ad
    return "German-language ad without an explicit statement that German is optional"


_EITHER_OR = re.compile(
    r"(deutsch\w*|german)[\s\-]*(?:kenntnisse|sprachkenntnisse|skills)?\s*(?:und\s*/\s*oder|oder|or|and\s*/\s*or|bzw\.?)\s*"
    r"(?:\w+\s+){0,2}(englisch\w*|english)"
    r"|(englisch\w*|english)[\s\-]*(?:kenntnisse|skills)?\s*(?:und\s*/\s*oder|oder|or|and\s*/\s*or|bzw\.?)\s*"
    r"(?:\w+\s+){0,2}(deutsch\w*|german)", re.I)
_NO_COVER = re.compile(
    r"no cover[- ]?letter|without (?:a )?cover[- ]?letter|cover[- ]?letter (?:is )?(?:not|no longer) (?:required|needed|necessary)"
    r"|kein(?:e|en)? (?:motivationsschreiben|anschreiben|bewerbungsschreiben|motivationsbrief)"
    r"|(?:motivationsschreiben|anschreiben) (?:ist )?nicht (?:nötig|notwendig|erforderlich)", re.I)


_LETTER_WORD = re.compile(r"cover[- ]?letter|motivation(?:al)?[- ]?letter|letter of motivation|anschreiben|"
                          r"motivationsschreiben|bewerbungsschreiben|motivationsbrief|covering letter", re.I)


def _consistency(res: dict, description: str) -> None:
    """Deterministic fixes where the model's structured fields contradict the ad text."""
    ev = res.get("german_evidence") or ""
    # "Deutsch oder Englisch" / "und/oder": either language is enough → never a German blocker by level
    if res.get("german") in ("b2", "c1_fluent") and ev and _EITHER_OR.search(ev) and strict_quote(ev, description):
        res["german"] = "plus"
        res["blockers"] = [b for b in (res.get("blockers") or []) if b != "german_required"]
        res["_german_override"] = "either_or"
    if res.get("cover_letter") != "not_wanted" and (_NO_COVER.search(description or "") or
                                                    re.search(r"(cover|motivation) letter[^.;]{0,25}(not (needed|required|necessary)|no need|unnecessary)",
                                                              res.get("how_to_apply") or "", re.I)):
        res["cover_letter"] = "not_wanted"
    # "send your CV and a few sentences" is a short motivation, not a request for a cover letter
    if (res.get("cover_letter") == "required" and "short_motivation" in (res.get("apply_docs") or [])
            and not _LETTER_WORD.search(description or "")):
        res["cover_letter"] = "not_mentioned"


# ---------------------------------------------------------------- evidence checks (no reject on a guess)
# A blocker stands only if the model quoted the ad word for word (strict_quote) AND the quote really states it.

_FT = re.compile(r"vollzeit|full[\s-]?time|fulltime|38[,.]5|\b(?:3[5-9]|40)(?:[,.]\d)?\s*-?\s*(?:h\b|std|stunden|wochenstunden|"
                 r"hours?|hrs)|\b100\s*%|ganzt[äa]gig|ganztags", re.I)
_PT_OPTION = re.compile(r"teilzeit|part[\s-]?time|geringf[üu]gig|stundenausma(?:ss|ß) nach vereinbarung|"
                        r"flexibles? stundenausma|working student|werkstudent", re.I)
_GERMAN_WORD = re.compile(r"deutsch|german", re.I)
_GERMAN_LEVEL = re.compile(r"\bgut\w*|sehr gut|ausgezeichnet|exzellent|perfekt|verhandlungssicher|flie(?:ss|ß)end|"
                           r"muttersprach|native|fluent|fluency|proficien|excellent|very good|\bgood\b|strong|advanced|"
                           r"business[\s-]?level|\bb2\b|\bc1\b|\bc2\b|german[\s-]?speaking|deutschsprachig|"
                           r"in wort und schrift|written and spoken|spoken and written|verbal and written|fundiert\w*|solide\w*|"
                           r"\bsicher(?:e|en|er)\b|stilsicher\w*|versiert\w*|einwandfrei\w*", re.I)
_GERMAN_SOFT = re.compile(r"von vorteil|w[üu]nschenswert|ein plus|\bplus\b|nice to have|advantage|asset|preferred|"
                          r"beneficial|bonus|grundkenntnisse|basic|\bb1\b|\ba2\b|optional|not required|nicht erforderlich|"
                          r"keine voraussetzung|is not a must|no german", re.I)
_YEARS = re.compile(r"(\d{1,2}|one|two|three|four|five|six|seven|eight|ten|ein|eine|einem|zwei|drei|vier|f[üu]nf|"
                    r"sechs|acht|zehn)\s*(?:\+|plus)?\s*(?:[-–]\s*\d+\s*)?(?:\(\d+\)\s*)?(?:years?|yrs|jahre[n]?)", re.I)
_NUMWORD = {"one": 1, "ein": 1, "eine": 1, "einem": 1, "two": 2, "zwei": 2, "three": 3, "drei": 3, "four": 4, "vier": 4,
            "five": 5, "fünf": 5, "funf": 5, "six": 6, "sechs": 6, "seven": 7, "eight": 8, "acht": 8, "ten": 10, "zehn": 10}
_SENIOR_WORDS = re.compile(r"mehrj[äa]hrig|langj[äa]hrig|several years|many years|extensive (?:professional )?experience|"
                           r"umfangreiche berufserfahrung|\bsenior\b|head of|\blead\b|leiter|leitung|f[üu]hrungs|"
                           r"team lead|director|direktor|managerial experience|people management", re.I)
_EU = re.compile(r"\beu\b|\bewr\b|\beea\b|\befta\b|european union|europ[äa]ische|staatsb[üu]rger|citizen|nationalit|"
                 r"work permit|arbeitserlaubnis|arbeitsbewilligung|aufenthaltstitel|right to work|work authori[sz]|"
                 r"unrestricted|uneingeschr[äa]nkt|rot-wei(?:ss|ß)-rot|zugang zum (?:österreichischen )?arbeitsmarkt|"
                 r"access to the (?:austrian )?labou?r market|eligible to work", re.I)
_REMOTE_WORD = re.compile(r"remote|home\s*office|homeoffice|anywhere|hybrid", re.I)
_MONTHS = {m: i for i, ms in enumerate([("jan", "jän", "jaen"), ("feb",), ("mar", "mär", "maer"), ("apr",), ("may", "mai"),
                                        ("jun",), ("jul",), ("aug",), ("sep",), ("oct", "okt"), ("nov",), ("dec", "dez")], 1)
           for m in ms}


def _dates_in(q: str) -> list[date]:
    out = []
    for d, m, y in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", q):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for y, m, d in re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", q):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, mon, y in re.findall(r"\b(\d{1,2})\.?\s+([A-Za-zÄäÖöÜü]{3,})\.?\s+(20\d{2})", q):
        mi = _MONTHS.get(mon[:3].lower())
        if mi:
            try:
                out.append(date(int(y), mi, int(d)))
            except ValueError:
                pass
    for mon, d, y in re.findall(r"\b([A-Za-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})", q):
        mi = _MONTHS.get(mon[:3].lower())
        if mi:
            try:
                out.append(date(int(y), mi, int(d)))
            except ValueError:
                pass
    return out


def _german_clause_ok(q: str) -> bool:
    """The quote requires German above B1: a clause names German with a level (gute/sehr gute/fließend/B2/C1/fluent/
    German-speaking …) that is not softened ('von Vorteil', 'plus', 'B1'), and it is not 'Deutsch oder Englisch'.
    A level shared by several languages counts: "Fluent in English and German", "Deutsch- und Englisch-Kenntnisse
    auf C1-Niveau", "Ausgezeichnete Türkisch- und Deutschkenntnisse"; "Englisch fließend, Deutsch von Vorteil" does not."""
    if _EITHER_OR.search(q):
        return False
    for clause in re.split(r"[;.()\n•]|,\s", q):
        if not _GERMAN_WORD.search(clause):
            continue
        parts = re.split(r"\s(?:und|and|sowie|as well as|plus)\s|\s&\s", clause)
        german_parts = [p for p in parts if _GERMAN_WORD.search(p)]
        if any(_GERMAN_SOFT.search(p) for p in german_parts):
            continue  # German itself is softened
        if any(_GERMAN_LEVEL.search(p) for p in german_parts):
            return True
        # the level stands next to the other language(s) of the same clause and applies to all of them
        if _GERMAN_LEVEL.search(clause) and not _GERMAN_SOFT.search(clause):
            return True
    return False


# part-time mentions that are NOT an option to do this job with ≤ 25 h (01.11.2026)
_PT_NOT_OPTION = re.compile(r"special cases|exceptional|exception|in ausnahmef[äa]llen|ausnahmsweise|besonderen f[äa]llen|"
                            r"elternteilzeit|altersteilzeit|bildungsteilzeit|teilzeitstudi|berufsbegleitend|"
                            r"part[\s-]?time (?:stud\w*|mba|master\w*|bachelor\w*|degree|program\w*|course\w*)|"
                            r"studying part[\s-]?time", re.I)
# "salary on a full-time basis (38.5 h)" — says nothing about the hours of this job
_FT_SALARY = re.compile(r"vollzeit-?basis|basis (?:einer )?vollzeit|bei vollzeit|vollzeit-?[äa]quivalent|"
                        r"full[\s-]?time (?:basis|equivalent)|\bfte\b", re.I)
_SALARY_CTX = re.compile(r"gehalt|salary|brutto|gross|entgelt|verg[üu]tung|entlohnung|bezahlung|kollektivvertrag|"
                         r"\bkv\b|\bpay\b|remuneration|€|\beur\b|euro\b", re.I)
_STUDENT_ROLE = re.compile(r"praktik|\bintern\b|internship|werkstudent|working student|\bstudent|trainee|teilzeit|"
                           r"part[\s-]?time|geringf[üu]gig|ferial|aushilf|nebenjob|minijob|studentische", re.I)
_EMPLOYMENT_KEYS = ("employment_types", "job_type", "employment_type", "time_type", "schedule", "commitment", "hours")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+|•", text or "") if s.strip()]


_WEEK_CTX = re.compile(r"woche|w[öo]chentlich|week|wochenstunden|stundenausma|ausma(?:ss|ß)|/\s*w\b|p\.\s*w\.|"
                       r"teilzeit|part[\s-]?time|vollzeit|full[\s-]?time", re.I)


def _weekly_min(text: str) -> int | None:
    """Lowest weekly-hours figure stated in sentences about the working week (not '24h support', 'Kernzeit 4 Std')."""
    lows = [r[0] for s in _sentences(text) if _WEEK_CTX.search(s) or len(s) <= 40
            for r in [weekly_hours_range(s)] if r]
    return min(lows) if lows else None


def _pt_option(job: dict, need_hours: bool = False) -> bool:
    """The ad offers a way to do THIS job with ≤ 25 h/week: part-time / working student in the title or the
    employment fields, or a sentence that names part-time ("Teilzeit möglich", "part-time (20 h) possible").
    Hours written right after the part-time word decide ("Teilzeit 30 Stunden" is not an option); without them the
    weekly hours stated anywhere in the ad decide (all > 25 → not an option). Never an option: part-time studies,
    parental / old-age part-time, "only in special cases". Before 01.11.2026 any part-time word anywhere in the ad
    plus any number ≤ 25 next to "h" anywhere ("24h support", "Kernzeit 4 Stunden") cancelled a proven
    full-time blocker."""
    meta = job.get("meta") or {}
    desc = job.get("description") or ""
    head = [job.get("title") or ""] + [str(meta.get(k) or "") for k in _EMPLOYMENT_KEYS] \
        + [str(v) for v in (meta.get("criteria") or {}).values()]
    week_min = _weekly_min(" . ".join([job.get("title") or ""] + _sentences(desc)))
    for s in head + _sentences(desc):
        m = _PT_OPTION.search(s)
        if not m or _PT_NOT_OPTION.search(s):
            continue
        rng = weekly_hours_range(s[m.start():m.start() + 70])  # hours stated for the part-time option itself
        if rng:
            if rng[0] <= MAX_H:
                return True
            continue
        if not need_hours and (week_min is None or week_min <= MAX_H):
            return True
    return False


def _ok_fulltime(q: str, job: dict, res: dict, today: date) -> bool:
    rng = weekly_hours_range(q)
    if rng and rng[0] <= MAX_H:
        return False  # the quote itself shows ≤ MAX_H h
    if not (_FT.search(q) or (rng and rng[0] > MAX_H)):
        return False
    if rng and rng[0] > MAX_H and not _FT_SALARY.search(q):
        # the quote states the weekly hours (e.g. "30h/Woche"): only a part-time option with ≤ 25 h written next to
        # it cancels it — a bare "Teilzeit" label is consistent with 30 h
        return not _pt_option(job, need_hours=True)
    if _FT_SALARY.search(q) and _SALARY_CTX.search(q):
        # "Gehalt … auf Vollzeitbasis (38,5 h)": Austrian pay transparency, stated for part-time and internship ads
        # too. It proves full-time only for a regular job whose ad offers no student / part-time angle at all.
        meta = job.get("meta") or {}
        head = " ".join([job.get("title") or ""] + [str(meta.get(k) or "") for k in _EMPLOYMENT_KEYS])
        if _STUDENT_ROLE.search(head):
            return False
    return not _pt_option(job)


def _fulltime_from_text(job: dict) -> str:
    """Code-side proof of full-time: a sentence of the ad (or a non-LinkedIn employment field) that states full-time /
    > 25 h, when the ad has no part-time option. LinkedIn's 'Employment type: Full-time' is not used (it is set on
    almost every posting, internships included)."""
    meta = job.get("meta") or {}
    fields = [f"{k}: {meta[k]}" for k in _EMPLOYMENT_KEYS if meta.get(k)]
    for s in fields + _sentences(job.get("description") or ""):
        if len(s) > 400:
            continue
        if _ok_fulltime(s, job, {}, date.today()):
            return s[:200]
    return ""


def _ok_german(q: str, job: dict, res: dict, today: date) -> bool:
    return _german_clause_ok(q)


def _ok_senior(q: str, job: dict, res: dict, today: date) -> bool:
    years = []
    for m in _YEARS.finditer(q):
        v = m.group(1).lower()
        years.append(int(v) if v.isdigit() else _NUMWORD.get(v, 0))
    if years:
        return max(years) >= 3
    return bool(_SENIOR_WORDS.search(q))


def _ok_not_vienna(q: str, job: dict, res: dict, today: date) -> bool:
    if VIENNA_RE.search(q) or _REMOTE_WORD.search(q) or location_ok(job.get("location") or ""):
        return False
    return True


def _ok_eu(q: str, job: dict, res: dict, today: date) -> bool:
    return bool(_EU.search(q))


def _ok_expired(q: str, job: dict, res: dict, today: date) -> bool:
    ds = _dates_in(q)
    return bool(ds) and max(ds) < today


def _ok_any(q: str, job: dict, res: dict, today: date) -> bool:
    return True  # wrong_field / not_eligible: semantic — the verbatim quote is the proof


CHECKS = {"fulltime_only": _ok_fulltime, "german_required": _ok_german, "senior": _ok_senior,
          "not_vienna": _ok_not_vienna, "eu_only": _ok_eu, "expired": _ok_expired, "wrong_field": _ok_any,
          "not_eligible": _ok_any}
_CHECK_HINT_TEMPLATE = {
    "fulltime_only": "quote must state full-time / Vollzeit / 38,5–40 h or a minimum above {{M}} h, and the ad must not "
                     "offer a part-time option of ≤ {{M}} h",
    "german_required": "quote must name German together with a level above B1 (gute/sehr gute/fließend/B2/C1/fluent/"
                       "German-speaking …), not 'von Vorteil'/'plus'/'B1' and not 'Deutsch oder Englisch'",
    "senior": "quote must state 3+ years of experience (or 'mehrjährige'/'several years') or a lead/head role",
    "not_vienna": "quote must name the place of work outside {{CITY}} (and not remote/hybrid)",
    "eu_only": "quote must require EU/EEA citizenship or an existing work permit / labour-market access",
    "expired": "quote must contain the deadline date, and that date must be before today",
    "wrong_field": "quote must show the field of work",
    "not_eligible": "quote must state who may apply",
}
_BLOCKER_LABEL_TEMPLATE = {"fulltime_only": "full-time / more than {{M}} h", "german_required": "German above B1",
                           "senior": "3+ years of experience", "wrong_field": "unrelated field",
                           "not_vienna": "outside {{CITY}}", "eu_only": "EU citizenship / work permit only",
                           "not_eligible": "wrong target group", "expired": "deadline passed"}
CHECK_HINT: dict[str, str] = {}
BLOCKER_LABEL: dict[str, str] = {}  # short human labels of the stop factors


# ---------------------------------------------------------------- candidate / region settings (config.yaml)
# Defaults = the original setup (student in Vienna: ≤ 20 h/week wanted, up to 25 h tolerated, €1,200 minimum).
MAX_H = 25          # candidate.max_weekly_hours: more than this → fulltime_only blocker
TARGET_H = 20       # candidate.target_weekly_hours: what the candidate actually wants (salary is pro-rated to it)
CANDIDATE: dict = {"name": "the candidate", "first": "The candidate", "pitch": "", "city": "Vienna",
                   "country_code": "AT", "timezone": "Europe/Vienna"}


def _render(s: str, v: dict) -> str:
    for k, val in v.items():
        s = s.replace("{{" + k + "}}", str(val))
    return s


def _render_obj(o, v: dict):
    if isinstance(o, dict):
        return {k: _render_obj(x, v) for k, x in o.items()}
    if isinstance(o, list):
        return [_render_obj(x, v) for x in o]
    return _render(o, v) if isinstance(o, str) else o


def configure(settings=None) -> None:
    """Apply candidate.* / location.* / ai.extra_scoring_rules from config.yaml to prompts and checks.
    Called by AI() at start-up; without settings the defaults above are used (unit tests)."""
    global MAX_H, TARGET_H, SCORE_SYSTEM, EMAIL_SYSTEM, SCORE_SCHEMA, EMAIL_SCHEMA
    get = (lambda k, d=None: settings.get(k, d)) if settings is not None else (lambda k, d=None: d)
    MAX_H = int(get("candidate.max_weekly_hours", 25))
    TARGET_H = int(get("candidate.target_weekly_hours", 20))
    name = get("candidate.name") or "the candidate"
    CANDIDATE.update(name=name, first=(name.split()[0] if get("candidate.name") else "The candidate"),
                     pitch=get("candidate.pitch", "") or "", city=get("location.city", "Vienna"),
                     country_code=get("location.country_code", "AT"), timezone=get("general.timezone", "Europe/Vienna"))
    sal = int(get("candidate.min_monthly_salary", 1200))
    rules = get("ai.extra_scoring_rules", []) or []
    v = {"T": TARGET_H, "T1": TARGET_H + 1, "M": MAX_H, "M1": MAX_H - 1, "CITY": CANDIDATE["city"],
         "NAME": name, "FIRST": CANDIDATE["first"], "TZ": CANDIDATE["timezone"],
         "SAL": f"{sal:,}",
         "EX1": f"{round(2600 * TARGET_H / 38.5):,}", "EX2": f"{round(15 * TARGET_H * 4.33):,}",
         "EXTRA_RULES": "".join(f"{r.strip()}\n" for r in rules if str(r).strip()),
         "WHY_EXAMPLE": get("ai.why_example") or "B2B sales: met a EUR 50k monthly target — matches the ad's sales-quota requirement"}
    SCORE_SYSTEM = _render(SCORE_SYSTEM_TEMPLATE, v)
    EMAIL_SYSTEM = _render(EMAIL_SYSTEM_TEMPLATE, v)
    SCORE_SCHEMA = _render_obj(SCORE_SCHEMA_TEMPLATE, v)
    EMAIL_SCHEMA = _render_obj(EMAIL_SCHEMA_TEMPLATE, v)
    CHECK_HINT.clear(); CHECK_HINT.update(_render_obj(_CHECK_HINT_TEMPLATE, v))
    BLOCKER_LABEL.clear(); BLOCKER_LABEL.update(_render_obj(_BLOCKER_LABEL_TEMPLATE, v))


configure()


def evidence_text(job: dict) -> str:
    meta = job.get("meta") or {}
    extra = " ".join(f"{k}: {meta.get(k)}" for k in ("employment_types", "job_type", "employment_type", "salary",
                                                      "hours", "schedule", "commitment", "time_type", "level",
                                                      "workplace", "deadline", "valid_through") if meta.get(k))
    crit = " ".join(f"{k} {v}" for k, v in (meta.get("criteria") or {}).items())
    return f"{job.get('title') or ''}\n{job.get('company') or ''}\n{job.get('location') or ''}\n{extra}\n{crit}\n" \
           f"{job.get('description') or ''}"


def verify_blockers(res: dict, job: dict, today: date | None = None) -> list[str]:
    """Keep only blockers proven by a verbatim, meaningful quote. Returns the claims that were dropped."""
    today = today or datetime.now(timezone.utc).date()
    text = evidence_text(job)
    claims = set(res.get("blockers") or []) - {"german_ad"}
    if res.get("german") in ("b2", "c1_fluent"):
        claims.add("german_required")
    if res.get("role_type") == "full_time" and res.get("hours_ok") == "no":
        claims.add("fulltime_only")
    quotes: dict[str, list[str]] = {}
    for e in res.get("evidence") or []:
        if isinstance(e, dict) and e.get("quote"):
            quotes.setdefault(e.get("blocker") or "", []).append(str(e["quote"]))
    if res.get("german_evidence"):
        quotes.setdefault("german_required", []).append(res["german_evidence"])
    kept, dropped, proof = [], [], {}
    hrs = weekly_hours_range(res.get("hours") or "")
    silent = set()
    if hrs and hrs[0] > MAX_H and "fulltime_only" not in claims:
        claims.add("fulltime_only")
        silent.add("fulltime_only")
    for b in sorted(claims):
        check = CHECKS.get(b, _ok_any)
        q = next((q for q in quotes.get(b, []) if strict_quote(q, text) and check(q, job, res, today)), None)
        if not q and b == "fulltime_only":
            q = _fulltime_from_text(job)
        if not q and b in silent:
            continue
        if q:
            kept.append(b)
            proof[b] = q[:200]
        else:
            dropped.append(b)
    res["blockers"] = kept
    res["_proof"] = proof
    if "german_required" in dropped and res.get("german") in ("b2", "c1_fluent"):
        res["german"] = "unclear"
    if "fulltime_only" in dropped and res.get("hours_ok") == "no":
        res["hours_ok"] = "unclear"
    return dropped


def english_workplace(company: str, patterns: list[str]) -> bool:
    c = (company or "").lower()
    return any(p and re.search(p, c, re.I) for p in patterns)


def _add_risk(res: dict, note: str, first: bool = True) -> None:
    risks = [r for r in (res.get("risks") or []) if r != note]
    res["risks"] = ([note] + risks if first else risks + [note])[:4]


def _enforce_blockers(res: dict, description: str = "", job: dict | None = None,
                      english_companies: list[str] | None = None, today: date | None = None) -> list[str]:
    """Deterministic post-processing of the model's verdict. Returns unverified claims (for one re-ask)."""
    job = job or {"description": description}
    description = description or job.get("description") or ""
    _consistency(res, description)
    dropped = verify_blockers(res, job, today)
    blockers = set(res.get("blockers") or [])
    if german_ad_check(res, description):
        if english_companies and english_workplace(job.get("company") or res.get("company_clean") or "",
                                                   english_companies):
            res["_german_ad_exempt"] = True
            _add_risk(res, "⚠️ The ad is in German and states no German requirement; the company's working language "
                           "is English — ask the recruiter")
        else:
            blockers.add("german_ad")
            if "German requirement" in (res.get("missing") or []):
                res["missing"] = [m for m in res["missing"] if m != "German requirement"]
            if res.get("_german_ok_unverified"):
                dropped.append("german_optional_quote")
    rng = weekly_hours_range(res.get("hours") or "")
    if rng and TARGET_H < rng[0] <= MAX_H and "fulltime_only" not in blockers:
        _add_risk(res, f"⏱ more than {TARGET_H} h — propose {TARGET_H} h/week to the employer")
        if res.get("hours_ok") == "no":
            res["hours_ok"] = "yes"
    res["blockers"] = sorted(blockers)
    return dropped


def triage_verdict(t: dict, job: dict) -> tuple[bool, str]:
    """(reject?, explanation). A triage reject stands only with a category and evidence found in the TITLE."""
    if not t.get("obviously_irrelevant"):
        return False, ""
    cat, ev, reason = t.get("category") or "none", (t.get("evidence") or "").strip(), t.get("reason") or ""
    title = job.get("title") or ""
    if cat not in TRIAGE_CATEGORIES[:3]:
        return False, f"no category ({reason})"
    if cat == "unrelated_field" and (_SPECULATIVE.search(reason) or _SPECULATIVE.search(ev)):
        return False, f"speculative ({reason})"  # (senior/apprenticeship evidence is checked by regex below)
    if not ev or not (strict_quote(ev, title) or (len(ev) >= 3 and ev.lower() in title.lower())):
        return False, f"evidence «{ev}» not in title ({reason})"
    if cat == "senior_title" and not _SENIOR_TITLE.search(ev):
        return False, f"«{ev}» is not a senior title"
    if cat == "senior_title" and _ENTRY_TITLE.search(title):
        return False, f"entry-level word in the title ({title[:60]})"  # "Junior to Senior", "Assistenz der GF"
    if cat == "apprenticeship_or_pupils" and not _APPRENTICE.search(ev):
        return False, f"«{ev}» is not an apprenticeship"
    return True, f"{cat}: «{ev}» — {reason}"[:200]


_SPECULATIVE = re.compile(r"\b(likely|unlikely|probably|implies|implied|may|might|presumably|possibly|seems|suggests|"
                          r"appears|could|wahrscheinlich|vermutlich|eventuell|vielleicht)\b", re.I)
_SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|head|lead|leiter\w*|leitung|director|direktor\w*|principal|chief|vp|"
                           r"vice president|partner|gesch[äa]ftsf[üu]hr\w*|managing director|abteilungsleit\w*|"
                           r"bereichsleit\w*|teamleit\w*|team ?lead\w*|filialleit\w*)\b", re.I)
_ENTRY_TITLE = re.compile(r"\b(junior|trainee|intern|internship|praktik\w*|werkstudent\w*|working student|student\w*|"
                          r"assistant|assistenz\w*|associate|graduate|absolvent\w*|berufseinsteig\w*|entry|"
                          r"nachwuchs\w*|ferial\w*)", re.I)
_APPRENTICE = re.compile(r"lehrling|lehrstelle|\blehre\b|apprentic|sch[üu]ler|pupil|pflichtschul", re.I)


class AI:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        configure(settings)
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key, max_retries=3, timeout=90)
        self.model_triage = settings.get("ai.triage_model", "claude-haiku-5-5")
        self.model_score = settings.get("ai.score_model", "claude-haiku-5-5")
        self.model_email = settings.get("ai.email_model", "claude-haiku-5-5")
        self.model_text = settings.get("ai.text_model", "claude-haiku-5-5")

    # ------------------------------------------------------------ low level
    def _cost(self, model: str, usage: Any) -> tuple[int, int, float]:
        pin, pout, pcw, pcr = PRICES.get(model, PRICES["claude-haiku-5-5"])
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = (inp * pin + out * pout + cw * pcw + cr * pcr) / 1_000_000
        return inp + cw + cr, out, cost

    async def _json_call(self, kind: str, model: str, system: str, user: str, schema: dict, max_tokens: int,
                         effort: str = "low", thinking: bool = False) -> dict:
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if not thinking:
            kwargs["thinking"] = {"type": "disabled"}
        resp = await self.client.messages.create(**kwargs)
        tin, tout, cost = self._cost(model, resp.usage)
        self.db.add_usage(kind, tin, tout, cost)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                return json.loads(m.group(0))
            raise

    # ------------------------------------------------------------ jobs
    def _job_block(self, job: dict, max_desc: int) -> str:
        meta = job.get("meta") or {}
        extra = []
        for k in ("employment_types", "job_type", "employment_type", "salary", "hours", "schedule", "commitment",
                  "time_type", "level", "workplace", "applicants"):
            if meta.get(k):
                extra.append(f"{k}: {meta[k]}")
        if meta.get("criteria"):
            extra.append("linkedin criteria: " + "; ".join(f"{k}={v}" for k, v in meta["criteria"].items()))
        return (
            f"TITLE: {job.get('title','')}\nCOMPANY: {job.get('company','')}\nLOCATION: {job.get('location','')}\n"
            f"SOURCE: {job.get('source','')}\n" + ("\n".join(extra) + "\n" if extra else "")
            + ("NOTE: only a short snippet of the ad is available (the full text could not be loaded); judge hours/"
               "German from the title, salary and snippet, and list what is not visible in \"missing\".\n"
               if meta.get("snippet_only") else "")
            + f"DESCRIPTION:\n{clip(job.get('description') or '(no description available)', max_desc)}"
        )

    def _feedback_block(self) -> str:
        fb = self.db.recent_feedback(int(self.settings.get("ai.feedback_examples", 20)))
        prefs = self.db.kv_get("prefs", []) or []
        lines = []
        if prefs:
            lines.append(f"{CANDIDATE['first']}'s explicit preferences (follow them):")
            lines += [f"- {p}" for p in prefs]
        if fb:
            lines.append(f"{CANDIDATE['first']}'s recent reactions to sent jobs (calibrate to them):")
            for f in fb:
                act = {"skip": "skipped", "applied": "APPLIED", "saved": "saved"}.get(f["action"], f["action"])
                why = f" — reason: {f['reason']}" if f.get("reason") else ""
                lines.append(f"- {act}: {clip(f.get('title') or '', 80)} @ {clip(f.get('company') or '', 40)}{why}")
        return "\n".join(lines)

    async def triage(self, job: dict) -> dict:
        user = self._job_block(job, 700)
        return await self._json_call("triage", self.model_triage, TRIAGE_SYSTEM + self.settings.profile_text, user,
                                     TRIAGE_SCHEMA, 300, effort="low", thinking=False)

    async def _score_call(self, job: dict, today: str, note: str = "") -> dict:
        fb = self._feedback_block()
        user = f"Today: {today}\n\n{self._job_block(job, int(self.settings.get('ai.max_description_chars', 9000)))}"
        if fb:
            user += f"\n\n---\n{fb}"
        if note:
            user += f"\n\n---\n{note}"
        res = await self._json_call(
            "score", self.model_score, SCORE_SYSTEM + self.settings.profile_text, user, SCORE_SCHEMA,
            int(self.settings.get("ai.score_max_tokens", 3000)),
            effort=self.settings.get("ai.score_effort", "low"),
            thinking=bool(self.settings.get("ai.score_thinking", True)),
        )
        _validate_score(res)
        return res

    def _post(self, res: dict, job: dict, today: str) -> list[str]:
        res["score"] = max(0, min(100, int(res.get("score", 0))))
        try:
            d = date.fromisoformat(today[:10])
        except ValueError:
            d = None
        dropped = _enforce_blockers(res, job.get("description") or "", job,
                                    list(self.settings.get("filters.english_workplace", []) or []), d)
        if res.get("blockers"):
            res["_raw_score"] = res["score"]  # fit without the stop factors: feeds the daily "cut off but promising" digest
            res["score"] = min(res["score"], 25)
        return dropped

    async def score(self, job: dict, today: str) -> dict:
        res = await self._score_call(job, today)
        dropped = self._post(res, job, today)
        if dropped and self.settings.get("ai.evidence_retry", True):
            # one re-ask: tell the model exactly which claims had no verbatim proof
            lines = []
            for b in dropped:
                if b == "german_optional_quote":
                    lines.append("- german_evidence: your sentence saying German is optional / English is enough is "
                                 "NOT in the ad word for word. Copy that sentence exactly, or, if the ad has no such "
                                 "sentence, set german='unclear' and german_evidence=''.")
                else:
                    lines.append(f"- {b}: no verbatim quote that proves it ({CHECK_HINT.get(b, '')}).")
            note = ("VERIFICATION of your previous answer failed:\n" + "\n".join(lines) +
                    "\nRe-read the ad. List a blocker only with an evidence quote copied word for word from the ad "
                    "that states it; if the ad does not state it, do NOT list it and score the job on its merits.")
            log.info("evidence re-ask for %s: %s", job.get("id"), ", ".join(dropped))
            first = {"score": res.get("score"), "blockers_claimed": dropped}
            res = await self._score_call(job, today, note)
            dropped2 = [b for b in self._post(res, job, today) if b != "german_optional_quote"]
            res["_reask"] = first
            if dropped2:
                res["_unverified"] = dropped2
                log.info("job %s: claims still without proof after re-ask, ignored: %s", job.get("id"), dropped2)
        return _sanitize(res)

    # ------------------------------------------------------------ e-mail
    async def classify_email(self, email: dict, tracker: list[dict]) -> dict:
        tl = "\n".join(f"- tracker_id={t['tracker_id']}: {t.get('company','')} — {t.get('title','')} ({t.get('status','')})"
                       for t in tracker[:80])
        user = (
            f"Tracked applications:\n{tl or '(none yet)'}\n\n---\nFROM: {email.get('from','')}\nTO: {email.get('to','')}\n"
            f"DATE: {email.get('date','')}\nSUBJECT: {email.get('subject','')}\n\n{clip(email.get('body',''), 5000)}"
        )
        return await self._json_call("email", self.model_email, EMAIL_SYSTEM, user, EMAIL_SCHEMA, 800, effort="low")

    # ------------------------------------------------------------ free text
    async def text(self, kind: str, system: str, user: str, max_tokens: int = 700) -> str:
        resp = await self.client.messages.create(
            model=self.model_text,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low"},
            thinking={"type": "disabled"},
        )
        tin, tout, cost = self._cost(self.model_text, resp.usage)
        self.db.add_usage(kind, tin, tout, cost)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    async def followup_draft(self, app: dict, extra: str = "") -> str:
        system = ("You write short, polite follow-up e-mails for a student job application. English, 70–110 words, "
                  f"no clichés, no subject line invented facts. Sign as '{CANDIDATE['name']}'. Output: first line "
                  "'Subject: ...', blank line, then the body.")
        user = (f"Company: {app.get('company')}\nPosition: {app.get('title')}\nApplied on: {app.get('applied_at','')}\n"
                f"Contact: {app.get('contact') or 'Hiring team'}\nKey strengths to mention briefly: "
                f"{CANDIDATE['pitch']} {extra}")
        return await self.text("followup", system, user, 500)

    async def find_company_posting(self, company: str, title: str, location: str = "",
                                   kind: str = "link_search") -> str | None:
        """Use Claude web search to find the posting on the company's own career site. Returns URL or None."""
        model = self.settings.get("links.search_model", "claude-haiku-5-5")
        location = location or CANDIDATE["city"]
        prompt = (
            f"Find the official job posting for \"{title}\" at {company} ({location}) on the company's OWN careers "
            f"website or its applicant-tracking system (Workday, SmartRecruiters, Greenhouse, Lever, SuccessFactors, "
            f"Personio, Recruitee, JOIN (join.com), etc.). Do NOT return LinkedIn, Indeed, Glassdoor, karriere.at, StepStone or other job "
            f"boards. If the exact posting is not found, return the company's careers/jobs page. "
            f"Answer with exactly one line: URL: <url>   or   URL: none"
        )
        try:
            resp = await self.client.messages.create(
                model=model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 2,
                    "blocked_domains": ["linkedin.com", "indeed.com", "glassdoor.com", "karriere.at", "stepstone.at",
                                        "stepstone.de", "xing.com", "kununu.com", "jooble.org", "talent.com",
                                        "careerjet.at", "careerjet.com", "jobviewtrack.com"],
                    "user_location": {"type": "approximate", "city": CANDIDATE["city"],
                                      "country": CANDIDATE["country_code"], "timezone": CANDIDATE["timezone"]},
                }],
            )
        except anthropic.APIError as e:
            log.warning("web search failed: %s", e)
            return None
        tin, tout, cost = self._cost(model, resp.usage)
        searches = getattr(getattr(resp.usage, "server_tool_use", None), "web_search_requests", 0) or 0
        self.db.add_usage(kind, tin, tout, cost + 0.01 * searches)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"URL:\s*(https?://\S+)", text)
        if not m:
            return None
        url = m.group(1).rstrip(").,;>]\"'")
        return url
