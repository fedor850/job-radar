"""Side jobs outside the field (shop assistant, receptionist, admin, desk security) — v1.3.0."""
import re
from datetime import date

from jobradar import ai
from jobradar.pipeline import Pipeline
from jobradar.telegram_ui import render_job


class _S:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


def _pipe(settings):
    p = Pipeline.__new__(Pipeline)
    p.settings = _S(settings)
    return p


def test_prompts_know_the_side_jobs():
    assert "security guards" not in ai.TRIAGE_SYSTEM
    assert "ALSO PASS ordinary light side jobs" in ai.TRIAGE_SYSTEM
    assert "Side jobs outside his field" in ai.SCORE_SYSTEM
    assert "{{" not in ai.SCORE_SYSTEM
    assert "niche" in ai.SCORE_SCHEMA["properties"] and "niche" in ai.SCORE_SCHEMA["required"]
    # physical work stays out and the language rule is repeated in the niche paragraph
    assert "cleaning" in ai.SCORE_SYSTEM and "german_required" in ai.SCORE_SYSTEM.split("(set \"niche\": true")[1]


def test_niche_only_for_ads_first_seen_after_the_start():
    p = _pipe({"niche.enabled": True, "niche.since": "2026-11-08T13:00:00+00:00"})
    assert p._niche_refusal({"first_seen": "2026-11-08T13:30:00+00:00"}) == ""
    assert p._niche_refusal({"first_seen": "2026-11-08T12:59:59+00:00"}).startswith("niche_old")
    assert p._niche_refusal({"first_seen": ""}).startswith("niche_old")
    off = _pipe({"niche.enabled": False, "niche.since": "2026-11-08T13:00:00+00:00"})
    assert off._niche_refusal({"first_seen": "2026-11-09T10:00:00+00:00"}).startswith("niche_off")
    assert _pipe({})._niche_refusal({"first_seen": "2026-11-09T10:00:00+00:00"}).startswith("niche_off")


def test_language_stays_a_hard_stop_for_niche_jobs():
    job = {"id": 1, "title": "Verkäufer:in Teilzeit", "company": "Shop", "location": "Wien", "meta": {},
           "description": "Wir suchen Verkäufer:in (20 Std.). Sehr gute Deutschkenntnisse erforderlich."}
    r = {"niche": True, "blockers": ["german_required"], "german": "c1_fluent", "hours_ok": "yes", "risks": [],
         "evidence": [{"blocker": "german_required", "quote": "Sehr gute Deutschkenntnisse erforderlich"}]}
    ai.verify_blockers(r, job, date(2026, 11, 9))
    assert r["blockers"] == ["german_required"]


def test_card_shows_the_label():
    job = {"title": "Shop Assistant", "company": "X", "score": 66, "analysis": {"niche": True}}
    assert "OUTSIDE YOUR FIELD" in render_job(job)
    job["analysis"] = {}
    assert "OUTSIDE YOUR FIELD" not in render_job(job)


def test_example_config_no_longer_hides_security():
    import pathlib
    txt = pathlib.Path(__file__).parent.parent.joinpath("examples", "config.example.yaml").read_text(encoding="utf-8")
    assert "sicherheitsdienst" not in txt
    pats = [re.compile(m.group(1), re.I) for m in re.finditer(r"^\s+- '(.+)'\s*$", txt, re.M) if "\\b" in m.group(1)]
    for title in ("Security-Mitarbeiter:in Empfang (m/w/d)", "Verkäufer:in Teilzeit", "Shop Assistant", "Rezeptionist:in"):
        assert not any(rx.search(title) for rx in pats), title
    assert any(rx.search("Reinigungskraft (m/w/d)") for rx in pats)
    assert any(rx.search("Zusteller:in Wien") for rx in pats)
