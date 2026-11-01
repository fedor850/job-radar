"""Apply-link resolution (prefer the company's own site) and referral search links."""
from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from urllib.parse import quote

from .sources.boards import ADAPTERS, detect_board
from .util import company_key, host_of, is_job_board, norm_title, slug_candidates

log = logging.getLogger("jobradar.links")

# LinkedIn school IDs come from config.yaml → referrals.linkedin_school_ids (alumni search buttons)
DEFAULT_SCHOOL_IDS: list[str] = []  # set referrals.linkedin_school_ids in config.yaml

SLUG_KINDS = ["greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "personio", "workable"]


def classify_link(url: str) -> str:
    """'company' (own site / ATS), 'board' (another job board), 'linkedin', or ''."""
    if not url:
        return ""
    h = host_of(url)
    if "linkedin.com" in h:
        return "linkedin"
    if is_job_board(url):
        return "board"
    return "company"


def link_rank(kind: str) -> int:
    return {"company": 3, "board": 2, "linkedin": 1}.get(kind, 0)


def best_link(*candidates: str) -> tuple[str, str]:
    best, best_kind = "", ""
    for url in candidates:
        if not url:
            continue
        k = classify_link(url)
        if link_rank(k) > link_rank(best_kind):
            best, best_kind = url, k
    return best, best_kind


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def match_in_db(db, company: str, title: str, exclude_uid: str = "") -> str | None:
    """A posting with the same company+similar title seen on another source with a company link."""
    for j in db.recent_jobs_by_company_key(company_key(company)):
        if j["uid"] == exclude_uid:
            continue
        for url in (j.get("apply_url"), j.get("url")):
            if url and classify_link(url) == "company" and title_similarity(j["title"], title) >= 0.82:
                return url
    return None


def guess_ats_link(company: str, title: str, timeout_budget: int = 14) -> tuple[str, tuple[str, str, dict]] | None:
    """Try common ATS slugs for the company and look for a posting with a matching title.

    Returns (posting_url, (kind, key, params)) or None. Blocking (network)."""
    tried = 0
    for slug in slug_candidates(company)[:3]:
        for kind in SLUG_KINDS:
            if tried >= timeout_budget:
                return None
            tried += 1
            params = {"no_location_filter": True}
            if kind == "personio":
                params["tld"] = "de"
            try:
                jobs = ADAPTERS[kind](slug, params, company)
            except Exception:  # noqa: BLE001
                continue
            best = None
            best_sim = 0.0
            for j in jobs:
                sim = title_similarity(j.title, title)
                if sim > best_sim:
                    best, best_sim = j, sim
            if best is not None and best_sim >= 0.82:
                return (best.apply_url or best.url), (kind, slug, {} if kind != "personio" else {"tld": "de"})
    return None


def google_search_url(company: str, title: str) -> str:
    q = f"{company} {title} careers"
    return "https://www.google.com/search?q=" + quote(q)


def referral_links(settings, company: str, company_linkedin: str = "") -> list[tuple[str, str]]:
    schools = settings.get("referrals.linkedin_school_ids", DEFAULT_SCHOOL_IDS) or DEFAULT_SCHOOL_IDS
    school_filter = quote(json.dumps([str(s) for s in schools], separators=(",", ":")))
    name = company.strip()
    links = [
        (
            "👥 " + (settings.get("referrals.alumni_label") or "Выпускники"),
            f"https://www.linkedin.com/search/results/people/?keywords={quote(name)}&schoolFilter={school_filter}"
            f"&origin=FACETED_SEARCH",
        ),
        (
            "🎯 Рекрутеры",
            "https://www.linkedin.com/search/results/people/?keywords="
            + quote(f'{name} (recruiter OR "talent acquisition" OR HR)'),
        ),
    ]
    if company_linkedin and "/company/" in company_linkedin:
        links.append(("🏢 Сотрудники", company_linkedin.rstrip("/") + "/people/"))
    return links


def learnable_board(url: str) -> tuple[str, str, dict] | None:
    return detect_board(url) if url else None
