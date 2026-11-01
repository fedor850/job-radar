"""Configuration: examples load, prompts render for other candidates / regions, CV checks use owner rules."""
import importlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Settings:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def get(self, dotted, default=None):
        node = self.cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@pytest.fixture
def ai():
    from jobradar import ai as mod
    yield mod
    mod.configure(None)  # back to defaults for the other tests


def test_example_files_load(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBRADAR_CONFIG", str(ROOT / "examples" / "config.example.yaml"))
    monkeypatch.setenv("JOBRADAR_PROFILE", str(ROOT / "examples" / "profile.example.md"))
    monkeypatch.setenv("JOBRADAR_SECRETS", str(ROOT / "examples" / "secrets.example.env"))
    monkeypatch.setenv("JOBRADAR_DATA", str(tmp_path))
    from jobradar import config, util
    s = config.load_settings()
    assert s.get("candidate.name") == "Alex Example"
    assert s.telegram_token.startswith("123456789:")
    assert "Alex Example" in s.profile_text
    assert util.location_ok("Wien, Österreich") and not util.location_ok("Graz")


def test_prompts_render_without_placeholders(ai):
    for cfg in ({}, {"candidate": {"name": "Alex Example"}}):
        ai.configure(_Settings(cfg))
        blob = ai.SCORE_SYSTEM + ai.EMAIL_SYSTEM + json.dumps(ai.SCORE_SCHEMA) + json.dumps(ai.EMAIL_SCHEMA) \
            + json.dumps(ai.CHECK_HINT) + json.dumps(ai.RU_BLOCKER, ensure_ascii=False)
        assert not re.search(r"\{\{[A-Z_0-9]+\}\}", blob)


def test_other_candidate_and_region(ai):
    ai.configure(_Settings({
        "candidate": {"name": "Maria Muster", "target_weekly_hours": 15, "max_weekly_hours": 30,
                      "min_monthly_salary": 900, "pitch": "Retail banking internship."},
        "location": {"city": "Berlin", "country_code": "DE"}, "general": {"timezone": "Europe/Berlin"},
        "ai": {"extra_scoring_rules": ["Prefer fintech start-ups."]},
    }))
    assert "MORE than 30 h/week" in ai.SCORE_SYSTEM and "≤15 h/week" in ai.SCORE_SYSTEM
    assert "outside the Berlin area" in ai.SCORE_SYSTEM and "Prefer fintech start-ups.\n" in ai.SCORE_SYSTEM
    assert "€900" in ai.SCORE_SYSTEM and "Maria Muster" in ai.EMAIL_SYSTEM
    assert "Europe/Berlin" in json.dumps(ai.EMAIL_SCHEMA)
    assert ai.RU_BLOCKER["not_vienna"] == "не Berlin" and "30" in ai.RU_BLOCKER["fulltime_only"]
    assert "Vienna" not in ai.SCORE_SYSTEM
    # the deterministic hours check follows the configured maximum
    job = {"id": 1, "title": "Analyst", "company": "X", "location": "Berlin", "description": "Teilzeit, 28 Stunden/Woche.",
           "meta": {}}
    r = {"blockers": ["fulltime_only"], "evidence": [{"blocker": "fulltime_only", "quote": "Teilzeit, 28 Stunden/Woche"}]}
    ai.verify_blockers(r, job)
    assert r["blockers"] == []  # 28 h ≤ 30 h


def test_home_region_patterns():
    from jobradar import util
    try:
        util.set_home_patterns(["berlin", "potsdam"], ["germany", "deutschland", "de"])
        assert util.location_ok("Potsdam") and util.location_ok("Remote, Germany")
        assert not util.location_ok("Wien")
    finally:
        util.set_home_patterns(None, ["austria", "österreich", "osterreich", "at"])
    assert util.location_ok("Wien") and not util.location_ok("Berlin")


def test_cv_builder_owner_rules(monkeypatch):
    monkeypatch.setenv("CV_OWNER", str(ROOT / "cv_builder" / "owner.example.json"))
    pytest.importorskip("docx")
    import sys
    sys.path.insert(0, str(ROOT / "cv_builder"))
    try:
        cvb = importlib.reload(importlib.import_module("cv_builder"))
    finally:
        sys.path.pop(0)
    assert cvb.FULL_NAME == "Alex Example" and cvb.FILE_PREFIX == "Alex_Example"
    spec = json.loads((ROOT / "cv_builder" / "examples" / "cv_example.json").read_text(encoding="utf-8"))
    errors, _ = cvb.check_spec(spec, "cv")
    assert errors == []
    spec["sections"][0]["text"] += " linkedin.com/in/alex"
    errors, _ = cvb.check_spec(spec, "cv")
    assert errors and "LinkedIn" in errors[0]
