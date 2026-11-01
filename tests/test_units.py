import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.sources.boards import detect_board
from jobradar.util import fingerprint, norm_title, company_key, slug_candidates, parse_relative_age, location_ok
from jobradar.links import classify_link, best_link
from jobradar.tracker import should_change, label_to_status

def test_detect():
    cases = {
        "https://job-boards.eu.greenhouse.io/bitpanda/jobs/5012230101": ("greenhouse","bitpanda"),
        "https://boards.greenhouse.io/n26/jobs/123": ("greenhouse","n26"),
        "https://jobs.lever.co/acme/abc-123": ("lever","acme"),
        "https://jobs.eu.lever.co/acme/abc": ("lever","acme"),
        "https://jobs.ashbyhq.com/fonio/a0f36795": ("ashby","fonio"),
        "https://jobs.smartrecruiters.com/DeloitteAT/744000152145339-junior": ("smartrecruiters","DeloitteAT"),
        "https://pwc.wd3.myworkdayjobs.com/en-US/Global_Campus_Careers/job/Wien/XYZ_123": ("workday","pwc/Global_Campus_Careers"),
        "https://freshfields.wd3.myworkdayjobs.com/FBD_101/job/Vienna/x": ("workday","freshfields/FBD_101"),
        "https://denzel-gruppe.jobs.personio.de/job/2824817": ("personio","denzel-gruppe"),
        "https://erstedigital.recruitee.com/l/en/o/internship": ("recruitee","erstedigital"),
        "https://apply.workable.com/acme/j/ABC123/": ("workable","acme"),
        "https://www.karriere.at/jobs/123": None,
    }
    for url, exp in cases.items():
        r = detect_board(url)
        got = (r[0], r[1]) if r else None
        assert got == exp, (url, got, exp)

def test_norm():
    assert norm_title("Praktikum Finance (m/w/d)") == norm_title("Praktikum Finance")
    assert norm_title("Junior Analyst (all genders)") == "junior analyst"
    assert company_key("Erste Group Bank AG") == "erste"
    assert company_key("REWE International Dienstleistungsgesellschaft mbH") == "rewe"
    assert fingerprint("Deloitte Österreich", "Praktikant:in (all genders) Tax") == fingerprint("Deloitte", "Praktikant:in Tax")
    assert "erstedigital" in slug_candidates("Erste Digital GmbH")

def test_age():
    assert parse_relative_age("Posted 30+ Days Ago") is not None
    assert parse_relative_age("vor 3 Tagen veröffentlicht") is not None
    assert parse_relative_age("Heute veröffentlicht") is not None

def test_links():
    assert classify_link("https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert classify_link("https://www.karriere.at/jobs/1") == "board"
    assert classify_link("https://jobs.smartrecruiters.com/x/1") == "company"
    assert best_link("https://www.linkedin.com/jobs/view/1", "https://careers.ey.com/ey/job/1")[1] == "company"

def test_loc():
    assert location_ok("Wien 5. Bezirk (Margareten)")
    assert location_ok("Wiener Neudorf, Lower Austria")
    assert not location_ok("Graz")
    assert not location_ok("Cenon-sur-Vienne, France")

def test_status():
    assert should_change("applied", "interview")
    assert not should_change("interview", "confirmed")
    assert should_change("interview", "rejected")
    assert label_to_status("🎤 Интервью") == "interview"

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)


def test_german_ad_rules():
    from jobradar.ai import _enforce_blockers
    de = ("Wir suchen für unser Team in Wien eine engagierte Persönlichkeit. Deine Aufgaben sind die Analyse von Daten "
          "und die Erstellung von Berichten für die Geschäftsführung. Du bist Student der Wirtschaft und hast erste "
          "Erfahrung mit Excel. Wir bieten dir ein junges Team, flexible Arbeitszeiten und eine faire Bezahlung. "
          "Wir freuen uns auf deine Bewerbung, die du uns über das Formular schicken kannst. ") * 2
    # German ad, nothing about languages -> blocked
    r = {"ad_language": "de", "german": "unclear", "german_evidence": "", "blockers": []}
    _enforce_blockers(r, de)
    assert "german_ad" in r["blockers"]
    # German ad with explicit "Deutsch oder Englisch" (even mislabelled c1 by the model) -> OK
    t = de + " Gute Deutsch- oder Englischkenntnisse in Wort und Schrift."
    r = {"ad_language": "de", "german": "c1_fluent", "german_evidence": "Gute Deutsch- oder Englischkenntnisse",
         "blockers": ["german_required"]}
    _enforce_blockers(r, t)
    assert r["blockers"] == [] and r["german"] == "plus"
    # German ad with "Deutsch von Vorteil" -> OK; invented quote -> blocked
    t2 = de + " Englisch ist unsere Arbeitssprache, Deutsch von Vorteil."
    r = {"ad_language": "de", "german": "plus", "german_evidence": "Englisch ist unsere Arbeitssprache, Deutsch von Vorteil",
         "blockers": []}
    _enforce_blockers(r, t2)
    assert r["blockers"] == []
    r = {"ad_language": "de", "german": "not_needed", "german_evidence": "English is our working language", "blockers": []}
    _enforce_blockers(r, de)
    assert "german_ad" in r["blockers"]
    # English ad -> rule does not apply
    en = ("We are looking for a working student to join our team in Vienna. You will support the finance team with "
          "reporting and analysis. You are studying business and you have strong Excel skills. We offer a great team. ") * 2
    r = {"ad_language": "en", "german": "unclear", "german_evidence": "", "blockers": []}
    _enforce_blockers(r, en)
    assert r["blockers"] == []
    # model says de but the text is clearly English (e.g. German job-board interface) -> not applied
    r = {"ad_language": "de", "german": "unclear", "german_evidence": "", "blockers": []}
    _enforce_blockers(r, "Beschreibung Arbeitspensum Stellenantritt " + en)
    assert r["blockers"] == []


def test_docs_label_and_cover_letter():
    from jobradar.ai import _enforce_blockers
    from jobradar.telegram_ui import docs_label, docs_request
    r = {"cover_letter": "required", "how_to_apply": "CV и 3 предложения; сопроводительного письма не нужно",
         "apply_docs": ["cv", "short_motivation"], "blockers": []}
    _enforce_blockers(r, "No portals, no cover-letter essays. Send us your CV and three sentences on why you.")
    assert r["cover_letter"] == "not_wanted"
    job = {"analysis": r}
    assert docs_label(job) == "CV + короткий текст"
    assert "НЕ нужен" in docs_request(job)
    assert docs_label({"analysis": {"cover_letter": "not_mentioned", "apply_docs": []}}) == "CV + письмо"


def test_short_motivation_is_not_cover_letter():
    from jobradar.ai import _enforce_blockers
    from jobradar.telegram_ui import docs_label
    r = {"cover_letter": "required", "apply_docs": ["cv", "short_motivation"], "how_to_apply": "", "blockers": []}
    _enforce_blockers(r, "If you are interested, send us your resume along with a few sentences explaining your interest.")
    assert r["cover_letter"] == "not_mentioned" and docs_label({"analysis": r}) == "CV + короткий текст"


def test_similar_dup_cross_source(tmp_path):
    from jobradar.pipeline import _words
    a = "Erste Group was founded in 1819 as the first Austrian savings bank. Within our Project Portfolio Governance team you will support"
    b = "Back to positions offered Erste Group was founded in 1819 as the first Austrian savings bank. Within our Project Portfolio Governance team you will support"
    c = "Completely different text about sales development representative selling software to customers"
    assert len(_words(a) & _words(b)) / len(_words(a) | _words(b)) >= 0.4
    assert len(_words(a) & _words(c)) / len(_words(a) | _words(c)) < 0.4


def test_linkedin_alert_parse():
    from jobradar.alerts import parse_alert, alert_jobs
    body = ("Your job alert for intern in Vienna\n\nConsulting Intern - Digital & AI\nINVERTO | A BCG Company\n"
            "Vienna, Vienna, Austria\nThis company is actively hiring\n"
            "View job: https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=a%3D\n\n----------\n\n"
            "Werkstudent Controlling\nErste Group\nVienna\n5 connections\n"
            "View job: https://www.linkedin.com/comm/jobs/view/4398765432/?x=1\n"
            "See all jobs: https://www.linkedin.com/comm/jobs/search/?keywords=intern\n")
    jobs = parse_alert(body)
    assert [j["id"] for j in jobs] == ["4312345678", "4398765432"], jobs
    assert jobs[0]["title"] == "Consulting Intern - Digital & AI" and jobs[0]["company"] == "INVERTO | A BCG Company"
    raws = alert_jobs([{"body": body + body}])  # same ids twice → once
    assert len(raws) == 2 and raws[0].uid == "linkedin:4312345678"  # same uid as the scraper → no double send


# ------------------------------------------------------------------ 30.10.2026: evidence, hours 21–25, new boards
def test_strict_quote():
    from jobradar.util import strict_quote
    t = "Wir bieten: Vollzeit (38,5 Std./Woche). Sehr gute Deutschkenntnisse in Wort und Schrift sind Voraussetzung."
    assert strict_quote("Vollzeit (38,5 Std./Woche)", t)
    assert strict_quote("sehr gute deutschkenntnisse in wort und schrift", t)
    assert strict_quote("Vollzeit … sehr gute Deutschkenntnisse", t)
    assert not strict_quote("Fluent German is required", t)            # invented
    assert not strict_quote("Deutschkenntnisse Vollzeit", t)            # scattered words, not a passage
    assert not strict_quote("Std", t)                                   # one short word proves nothing
    assert strict_quote("Gute Deutschkenntnisse", "GUTE DEUTSCHKENNTNISSE erforderlich")
    assert strict_quote("fließend Deutsch", "Sie sprechen fliessend Deutsch")   # ß / ss spelling


def _job(desc, title="Junior Analyst", location="Wien", company="ACME"):
    return {"id": 1, "title": title, "company": company, "location": location, "description": desc, "meta": {}}


def test_blockers_need_proof():
    from datetime import date
    from jobradar.ai import verify_blockers
    today = date(2026, 10, 30)
    desc = ("We are looking for a Junior Analyst in Vienna. This is a full-time position (40 hours/week). "
            "You have 2 years of experience in reporting. Good German skills are an advantage. Apply by 15.11.2026.")
    # invented / wrong quotes → dropped
    r = {"blockers": ["fulltime_only", "german_required", "senior", "expired"], "german": "c1_fluent",
         "evidence": [{"blocker": "fulltime_only", "quote": "This is a full-time position (40 hours/week)"},
                      {"blocker": "german_required", "quote": "Good German skills are an advantage"},
                      {"blocker": "senior", "quote": "You have 2 years of experience in reporting"},
                      {"blocker": "expired", "quote": "Apply by 15.11.2026"}]}
    dropped = verify_blockers(r, _job(desc), today)
    assert r["blockers"] == ["fulltime_only"], r
    assert set(dropped) == {"german_required", "senior", "expired"}, dropped
    assert r["german"] == "unclear"
    # a full-time claim with a part-time option of 20 h in the ad is not proven
    desc2 = "Full-time position. Part-time (20 hours) is also possible for students."
    r = {"blockers": ["fulltime_only"], "evidence": [{"blocker": "fulltime_only", "quote": "Full-time position"}]}
    assert verify_blockers(r, _job(desc2), today) == ["fulltime_only"] and r["blockers"] == []
    # Teilzeit only from 30 h → still a blocker
    desc3 = "Vollzeit oder Teilzeit ab 30 Wochenstunden möglich. Standort Wien."
    r = {"blockers": ["fulltime_only"], "evidence": [{"blocker": "fulltime_only", "quote": "Teilzeit ab 30 Wochenstunden"}]}
    assert verify_blockers(r, _job(desc3), today) == [] and r["blockers"] == ["fulltime_only"]
    # proven German / senior / expired / EU
    desc4 = ("Fließende Deutschkenntnisse sind Voraussetzung. Mindestens 5 Jahre Berufserfahrung. "
             "Bewerbungsfrist: 10.10.2026. Nur EU-Staatsbürger:innen können berücksichtigt werden.")
    r = {"blockers": ["german_required", "senior", "expired", "eu_only"], "evidence": [
        {"blocker": "german_required", "quote": "Fließende Deutschkenntnisse sind Voraussetzung"},
        {"blocker": "senior", "quote": "Mindestens 5 Jahre Berufserfahrung"},
        {"blocker": "expired", "quote": "Bewerbungsfrist: 10.10.2026"},
        {"blocker": "eu_only", "quote": "Nur EU-Staatsbürger:innen können berücksichtigt werden"}]}
    assert verify_blockers(r, _job(desc4), today) == [] and len(r["blockers"]) == 4
    # "Deutsch oder Englisch" never proves german_required
    r = {"blockers": ["german_required"], "evidence": [{"blocker": "german_required", "quote": "Sehr gute Deutsch- oder Englischkenntnisse"}]}
    assert verify_blockers(r, _job("Sehr gute Deutsch- oder Englischkenntnisse."), today) == ["german_required"]
    # not_vienna contradicts a Vienna location
    r = {"blockers": ["not_vienna"], "evidence": [{"blocker": "not_vienna", "quote": "Standort Graz"}]}
    assert verify_blockers(r, _job("Standort Graz oder Wien", location="Wien"), today) == ["not_vienna"]


def test_hours_21_25_and_english_exemption():
    from jobradar.ai import _enforce_blockers
    from jobradar.util import weekly_hours_range
    assert weekly_hours_range("25 ч/нед") == (25, 25)
    assert weekly_hours_range("20–30 h") == (20, 30)
    assert weekly_hours_range("ab 30 Wochenstunden") == (30, 30)
    r = {"blockers": [], "hours": "25 ч/нед", "hours_ok": "no", "risks": []}
    _enforce_blockers(r, "We offer 25 hours per week in Vienna.")
    assert r["blockers"] == [] and r["hours_ok"] == "yes" and r["risks"][0].startswith("⏱")
    de = ("Wir suchen für unser Team in Wien eine engagierte Persönlichkeit. Deine Aufgaben sind die Analyse von Daten "
          "und die Erstellung von Berichten für die Geschäftsführung. Du bist Student der Wirtschaft und hast erste "
          "Erfahrung mit Excel. Wir bieten dir ein junges Team und eine faire Bezahlung. ") * 3
    r = {"ad_language": "de", "german": "unclear", "german_evidence": "", "blockers": [], "risks": []}
    _enforce_blockers(r, de, {"company": "Bitpanda GmbH", "description": de}, [r"bitpanda"])
    assert r["blockers"] == [] and "английский" in r["risks"][0]
    r = {"ad_language": "de", "german": "unclear", "german_evidence": "", "blockers": [], "risks": []}
    _enforce_blockers(r, de, {"company": "Wiener Linien", "description": de}, [r"bitpanda"])
    assert r["blockers"] == ["german_ad"]


def test_triage_needs_title_evidence():
    from jobradar.ai import triage_verdict
    job = {"title": "Senior Financial Controller (m/w/d)", "description": "Vollzeit, gute Deutschkenntnisse"}
    assert triage_verdict({"obviously_irrelevant": True, "category": "senior_title", "evidence": "Senior", "reason": "senior"}, job)[0]
    # hours / German are never triage reasons
    j2 = {"title": "Junior Controller", "description": "Vollzeit 38,5 h, sehr gute Deutschkenntnisse"}
    assert not triage_verdict({"obviously_irrelevant": True, "category": "none", "evidence": "Vollzeit", "reason": "full-time"}, j2)[0]
    assert not triage_verdict({"obviously_irrelevant": True, "category": "unrelated_field", "evidence": "Vollzeit 38,5 h", "reason": "x"}, j2)[0]
    # guesses are ignored
    j3 = {"title": "Junior Event Manager", "description": ""}
    assert not triage_verdict({"obviously_irrelevant": True, "category": "unrelated_field", "evidence": "Event", "reason": "likely needs German"}, j3)[0]
    assert triage_verdict({"obviously_irrelevant": True, "category": "unrelated_field", "evidence": "Koch", "reason": "kitchen"},
                          {"title": "Koch / Köchin (m/w/d)"})[0]
    assert not triage_verdict({"obviously_irrelevant": True, "category": "senior_title", "evidence": "Manager", "reason": "manager"},
                              {"title": "Account Manager"})[0]


def test_new_board_detection():
    assert detect_board("https://acme.teamtailor.com/jobs/123-sales")[:2] == ("teamtailor", "acme")
    assert detect_board("https://join.com/companies/acme/1234-intern")[:2] == ("join", "acme")
    assert detect_board("https://acme.bamboohr.com/careers/12")[:2] == ("bamboohr", "acme")


def test_german_levels_shared_and_soft():
    from jobradar.ai import _german_clause_ok as g
    assert g("Fluent in English and German language")
    assert g("Deutsch- und Englisch-Kenntnisse auf C1-Niveau ermöglichen Ihnen die Zusammenarbeit")
    assert g("Ausgezeichnete Türkisch- und Deutschkenntnisse")
    assert g("Sehr gute Deutschkenntnisse und Englisch von Vorteil")
    assert g("Verhandlungssichere Deutsch- und gute Englischkenntnisse")
    assert not g("Englisch fließend, Deutsch von Vorteil")
    assert not g("Fluent English; German is a plus")
    assert not g("Gute Deutsch- oder Englischkenntnisse")
    assert not g("German B1 is sufficient, fluent English required")
    assert not g("Deutschkenntnisse")


def test_fulltime_proof_2026_11_01():
    """Full-time jobs that slipped through as «ИИ заподозрил … проверь сам» (30.10–01.11.2026)."""
    from datetime import date
    from jobradar.ai import verify_blockers
    today = date(2026, 11, 1)

    def ft(desc, quote, title="Junior Analyst", meta=None):
        job = {"id": 1, "title": title, "company": "ACME", "location": "Wien", "description": desc, "meta": meta or {}}
        r = {"blockers": ["fulltime_only"], "evidence": [{"blocker": "fulltime_only", "quote": quote}]}
        verify_blockers(r, job, today)
        return r["blockers"] == ["fulltime_only"]

    # "40-hour week" / "37.5-hour" are hours; part-time "in special cases" is not an option
    assert ft("OSCE internships are full-time (40-hour week), but flexible arrangement for part-time internship can be "
              "approved in special cases;", "OSCE internships are full-time (40-hour week)", title="Internship")
    assert ft("The salary is based on a 37.5-hour working week.", "based on a 37.5-hour working week")
    # part-time words elsewhere in the ad (studies, 24h support, other jobs) do not cancel a full-time job
    assert ft("This is a full-time position (40 hours/week). We support part-time MBA studies. Our 24h support desk.",
              "full-time position (40 hours/week)")
    assert ft("Vollzeit (38,5 h), Gleitzeit, Kernzeit 4 Stunden.", "Vollzeit (38,5 h)")
    # a quote of an employment field
    assert ft("Kurzbeschreibung.", "employment_types: Vollzeit", meta={"employment_types": "Vollzeit"})
    # code finds the proof itself when the model gave no quote
    job = {"id": 1, "title": "Luftfracht Spezialist", "company": "ACS", "location": "Wien", "description": "Kurz.",
           "meta": {"employment_type": "Vollzeit"}}
    r = {"blockers": ["fulltime_only"], "evidence": []}
    verify_blockers(r, job, today)
    assert r["blockers"] == ["fulltime_only"]
    # ... but not from LinkedIn's "Employment type: Full-time" (set on internships too)
    job = {"id": 1, "title": "Business Intern", "company": "X", "location": "Wien", "description": "Great internship.",
           "meta": {"criteria": {"Employment type": "Full-time"}}}
    r = {"blockers": ["fulltime_only"], "evidence": []}
    verify_blockers(r, job, today)
    assert r["blockers"] == []
    # a real part-time option of ≤ 25 h still cancels it; "Teilzeit 30 Stunden" does not
    assert not ft("Full-time position. Part-time (20 hours) is also possible for students.", "Full-time position")
    assert not ft("Vollzeit oder Teilzeit möglich.", "Vollzeit")
    assert ft("Wir suchen ein Team (30h/Woche) für den Sommer.", "Team (30h/Woche)", title="Rezeption Teilzeit",
              meta={"job_type": "parttime"})
    # salary "auf Vollzeitbasis" proves nothing for an internship / part-time ad, but does for a regular job
    sal = "Das Gehalt beträgt € 2.600 brutto auf Vollzeitbasis (38,5 h)."
    assert not ft(sal, "€ 2.600 brutto auf Vollzeitbasis (38,5 h)", title="Praktikum Controlling")
    assert ft(sal, "€ 2.600 brutto auf Vollzeitbasis (38,5 h)", title="CRM Manager")
    assert ft("Wir suchen Unterstützung im Game Design auf Vollzeitbasis!", "Unterstützung im Game Design auf Vollzeitbasis",
              title="Game Designer")
    # the model writes > 25 h in "hours" without claiming the blocker: the code checks the ad itself
    job = {"id": 1, "title": "Office Manager", "company": "X", "location": "Wien",
           "description": "Anstellung in Vollzeit (40 Stunden).", "meta": {}}
    r = {"blockers": [], "hours": "40 ч/нед", "evidence": []}
    assert verify_blockers(r, job, today) == [] and r["blockers"] == ["fulltime_only"]
    r = {"blockers": [], "hours": "40 ч/нед", "evidence": []}
    job["description"] = "Teilzeit möglich, 20 Stunden pro Woche."
    assert verify_blockers(r, job, today) == [] and r["blockers"] == []


def test_senior_german_wording_2026_11_01():
    from datetime import date
    from jobradar.ai import verify_blockers
    today = date(2026, 11, 1)
    desc = ("At least 7 plus years of relevant experience in a similar role. Fundierte Deutschkenntnisse sind erforderlich. "
            "Englisch fließend, Deutsch von Vorteil.")
    job = {"id": 1, "title": "Manager", "company": "X", "location": "Wien", "description": desc, "meta": {}}
    r = {"blockers": ["senior", "german_required"], "evidence": [
        {"blocker": "senior", "quote": "At least 7 plus years of relevant experience in a similar role"},
        {"blocker": "german_required", "quote": "Fundierte Deutschkenntnisse sind erforderlich."}]}
    assert verify_blockers(r, job, today) == [] and r["blockers"] == ["german_required", "senior"]
    r = {"blockers": ["german_required"], "evidence": [{"blocker": "german_required", "quote": "Englisch fließend, Deutsch von Vorteil"}]}
    assert verify_blockers(r, job, today) == ["german_required"]
