"""Remote job boards + remote-first employers (v1.2.0): region logic, parsers, role filter, board discovery."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.models import RawJob  # noqa: E402
from jobradar.sources import boards, remote  # noqa: E402
from jobradar.sources.boards import _keep, region_ok, role_ok  # noqa: E402


# ------------------------------------------------------------------ where a remote job can be done from
@pytest.mark.parametrize("place,ok", [
    ("", True), ("Remote", True), ("Worldwide", True), ("WorldWide", True), ("Anywhere in the World", True),
    ("Global", True), ("Europe", True), ("Remote - EMEA", True), ("EU", True), ("Austria", True), ("DACH", True),
    ("Europe or US", True), ("USA", False), ("USA Only", False), ("Latin America Only", False),
    ("Mexico", False), ("México", False), ("Colombia", False), ("Remote - Germany", False), ("Canada Only", False),
    ("Boston", False), ("los angeles", False), ("South Carolina", False), ("Harju maakond", False),
    ("Fully remote job", True), ("Remote, Poland", False), ("India", False),
])
def test_region_ok(place, ok):
    assert region_ok(place) is ok


def test_region_ok_takes_lists():
    assert region_ok([]) is True
    assert region_ok(["United States"]) is False
    assert region_ok(["Germany", "Austria", "Spain"]) is True
    assert region_ok(["Mexico", "Colombia"]) is False


# ------------------------------------------------------------------ boards: 'Worldwide' ads were lost before
def test_worldwide_and_global_ads_are_kept_on_every_board():
    assert _keep("Worldwide", "", False, {})
    assert _keep("WorldWide", "", False, {})
    assert _keep("Remote - Worldwide", "", False, {})
    assert _keep("Vienna", "", False, {})
    assert not _keep("Mexico", "", False, {})
    assert not _keep("Remote - US", "", True, {})


def test_remote_first_boards_keep_europe_without_the_word_remote_and_drop_other_countries():
    p = {"remote_first": True}
    assert _keep("EMEA", "", False, p)
    assert _keep("Europe", "", False, p)
    assert _keep("Worldwide", "", False, p)
    assert _keep("Remote", "", True, p)
    assert not _keep("Mexico City", "", False, p)
    assert not _keep("Colombia", "", False, p)
    assert not _keep("Remote - Mexico", "", True, p)
    # an ordinary board does not take a bare 'Europe' (it is an on-site office there)
    assert not _keep("Europe", "", False, {})


def test_no_remote_boards_stay_vienna_only():
    assert not _keep("Worldwide", "", False, {"no_remote": True})
    assert _keep("Wien", "", False, {"no_remote": True})


# ------------------------------------------------------------------ which titles are read
@pytest.mark.parametrize("title,ok", [
    ("Business Analyst Junior+ [Antifraud & Disputes]", True), ("Data Analyst Junior / Junior+ [Core Banking]", True),
    ("Risk Analyst Junior/Junior+ [Integrated Risk Management]", True), ("AI Creative Marketer (Intern)", True),
    ("Partnership Programs Associate", True), ("Customer Success Manager, EMEA", True),
    ("Sales Development Representative", True), ("Working Student Finance", True), ("FP&A Analyst", True),
    ("Chief of Staff", True), ("Account Executive - DACH", True),
    ("QA Engineer - Backend (Golang) Junior+", False), ("Senior Software Engineer", False),
    ("Product Designer", False), ("Data Engineer", False), ("Research Scientist Junior", False),
    ("Registered Nurse", False), ("Warehouse Associate", False), ("Staff Accountant Intern", True),
    ("DevOps Analyst", False), ("Truck Driver", False),
])
def test_role_ok(title, ok):
    assert role_ok(title) is ok


# ------------------------------------------------------------------ parsers (shapes taken from the live APIs)
def test_remotive_keeps_workable_regions_only():
    data = {"jobs": [
        {"id": 1, "url": "https://remotive.com/remote-jobs/sales/a", "title": "Account Executive", "company_name": "Acme",
         "candidate_required_location": "Worldwide", "job_type": "full_time", "publication_date": "2026-11-06T08:51:01",
         "description": "<p>Sell things. Apply at <a href='https://boards.greenhouse.io/acme/jobs/123'>here</a></p>"},
        {"id": 2, "url": "u", "title": "Nurse", "company_name": "VXI", "candidate_required_location": "USA",
         "job_type": "full_time", "publication_date": "2026-11-06T08:51:01", "description": "x"}]}
    jobs = remote.parse_remotive(data)
    assert [j.external_id for j in jobs] == ["remotive:1"]
    j = jobs[0]
    assert j.source == "remotejobs" and j.uid == "remotejobs:remotive:1" and j.company == "Acme"
    assert j.apply_url == "https://boards.greenhouse.io/acme/jobs/123"  # the employer's own ATS wins
    assert "Employment type: full time" in j.description and "Sell things" in j.description
    assert j.posted_at is not None and j.posted_at.tzinfo is not None and j.meta["workplace"] == "remote"


def test_remoteok_skips_legal_notice_and_restricted_places():
    data = [{"legal": "notice"},
            {"id": "5", "slug": "s", "position": "Customer Support", "company": "Foo", "location": "",
             "description": "<p>Help</p>", "date": "2026-11-07T00:00:02+00:00", "url": "https://remoteok.com/x"},
            {"id": "6", "position": "Account Manager", "company": "Bar", "location": "Boston", "description": "x"}]
    jobs = remote.parse_remoteok(data)
    assert [j.company for j in jobs] == ["Foo"] and jobs[0].apply_url == "https://remoteok.com/x"


def test_wwr_rss_region_and_company_split():
    xml = """<rss><channel>
    <item><title>GitLab: Executive Assistant</title><region>USA Only</region><country></country>
      <description>&lt;p&gt;x&lt;/p&gt;</description><pubDate>Sat, 08 Nov 2026 06:00:00 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/gitlab-ea</guid><link>https://weworkremotely.com/remote-jobs/gitlab-ea</link></item>
    <item><title>Acme: Customer Success Manager</title><region>Anywhere in the World</region><country></country>
      <type>Full-Time</type><description>&lt;p&gt;Hello &lt;a href="https://jobs.lever.co/acme/abc"&gt;apply&lt;/a&gt;&lt;/p&gt;</description>
      <pubDate>Sat, 08 Nov 2026 06:00:00 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/acme-csm</guid><link>https://weworkremotely.com/remote-jobs/acme-csm</link></item>
    </channel></rss>"""
    jobs = remote.parse_wwr(xml)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.company == "Acme" and j.title == "Customer Success Manager" and j.apply_url == "https://jobs.lever.co/acme/abc"
    assert j.posted_at is not None and j.posted_at.year == 2026 and "Employment type: Full-Time" in j.description


def test_himalayas_country_lists():
    base = {"title": "Pricing Analyst", "companyName": "Foo", "guid": "https://himalayas.app/companies/foo/jobs/p",
            "applicationLink": "https://himalayas.app/companies/foo/jobs/p?utm=x", "employmentType": "Full Time",
            "description": "<p>Numbers</p>", "pubDate": "1794127803"}
    wide = remote.parse_himalayas({"jobs": [{**base, "locationRestrictions": []}]})
    assert wide[0].location == "Remote (Worldwide)" and wide[0].posted_at.year == 2026
    europe = remote.parse_himalayas({"jobs": [{**base, "locationRestrictions": ["Austria", "Germany"]}]})
    assert europe[0].location == "Remote (Austria, Germany)"
    assert remote.parse_himalayas({"jobs": [{**base, "locationRestrictions": ["United States"]}]}) == []
    assert remote.parse_himalayas({"jobs": [{**base, "locationRestrictions": ["Mexico", "Colombia"]}]}) == []


def test_jobicy_and_workingnomads():
    ji = {"jobs": [{"id": 9, "url": "https://jobicy.com/jobs/9", "jobTitle": "Sales Associate", "companyName": "Z",
                    "jobGeo": "Anywhere", "jobType": ["Full-Time"], "jobDescription": "<p>d</p>",
                    "pubDate": "2026-11-08T05:54:43+00:00"},
                   {"id": 10, "url": "u", "jobTitle": "x", "companyName": "Z", "jobGeo": "USA", "jobDescription": "d"}]}
    assert [j.external_id for j in remote.parse_jobicy(ji)] == ["jobicy:9"]
    wn = [{"url": "https://www.workingnomads.com/job/go/1996233/", "title": "Content Reviewer", "company_name": "iMerit",
           "location": "Switzerland, Australia, Austria, Belgium", "description": "d", "pub_date": "2026-11-05T03:10:45-05:00"},
          {"url": "https://www.workingnomads.com/job/go/1/", "title": "T", "company_name": "C", "location": "USA, Canada",
           "description": "d", "pub_date": "2026-11-05T03:10:45-05:00"}]
    got = remote.parse_workingnomads(wn)
    assert len(got) == 1 and got[0].external_id == "workingnomads:1996233"


# ------------------------------------------------------------------ discovery of the employer's own board
def test_slug_candidates():
    assert remote.slug_candidates("Plata Card") == ["platacard", "plata-card", "plata"]
    assert remote.slug_candidates("Acme GmbH") == ["acme"]
    assert remote.slug_candidates("Bright Bar Technologies Inc.") == ["brightbar", "bright-bar", "bright"]
    assert remote.slug_candidates("Foo Bar Inc.") == ["foobar", "foo-bar"]  # a 3-letter first word is too generic
    assert remote.slug_candidates("") == []


class FakeDB:
    def __init__(self, boards_=None):
        self.kv, self.added, self._boards = {}, [], boards_ or []

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v

    def boards(self, enabled_only=True):
        return self._boards

    def upsert_board(self, kind, key, company, origin, params=None):
        self.added.append((kind, key, company, origin, params))
        return True


class Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass


def fake_board(titles):
    return lambda key, params, company: [RawJob(source="x", external_id=str(i), title=t, company=company)
                                         for i, t in enumerate(titles)]


def test_discover_accepts_a_board_that_has_the_same_job(monkeypatch):
    def broken(key, params, company):
        raise RuntimeError("404")
    monkeypatch.setattr(boards, "ADAPTERS", {"greenhouse": broken, "lever": broken,
                                             "ashby": fake_board(["Partnership Programs Associate", "Other"])})
    monkeypatch.setattr(remote, "ADAPTERS", boards.ADAPTERS)
    db = FakeDB()
    assert remote.discover_board("Plata Card", "Partnership Programs Associate", db, Log()) == ("ashby", "platacard")
    kind, key, company, origin, params = db.added[0]
    assert (kind, key, company, origin) == ("ashby", "platacard", "Plata Card", "remote-learned")
    assert params["remote_first"] is True and params["min_interval_sec"] == 1800


def test_discover_rejects_a_board_of_another_company(monkeypatch):
    monkeypatch.setattr(remote, "ADAPTERS", {"lever": fake_board(["Warehouse Manager", "Driver"])})
    db = FakeDB()
    assert remote.discover_board("Close", "Customer Success Manager", db, Log()) is None
    assert db.added == []


def test_discover_greenhouse_accepts_the_same_company_name(monkeypatch):
    monkeypatch.setattr(remote, "ADAPTERS", {"greenhouse": fake_board(["Something else"])})
    monkeypatch.setattr(remote.http, "get_json", lambda url, **kw: {"name": "Plata Card"})
    db = FakeDB()
    assert remote.discover_board("Plata Card, Inc.", "Other title", db, Log()) == ("greenhouse", "platacard")


# ------------------------------------------------------------------ the source
def make_source(db=None, **params):
    return remote.RemoteJobsSource(None, params, db=db)


def test_fetch_keeps_business_titles_dedupes_and_discovers(monkeypatch):
    def rj(site, ext, title, company):
        return RawJob(source="remotejobs", external_id=f"{site}:{ext}", title=title, company=company,
                      location="Remote (Worldwide)", url="https://x/" + ext, apply_url="https://x/" + ext)
    src = make_source(FakeDB(), sites=["a", "b"], discover_per_run=5)
    pages = {"a": [rj("a", "1", "Customer Success Manager", "Acme"), rj("a", "2", "Backend Engineer", "Acme")],
             "b": [rj("b", "3", "Customer Success Manager", "ACME Inc"), rj("b", "4", "Sales Associate", "Zed")]}
    monkeypatch.setattr(src, "_fetch_site", lambda site: pages[site])
    found = []
    monkeypatch.setattr(remote, "discover_board", lambda comp, title, db, log: found.append((comp, title)))
    jobs = src.fetch()
    assert sorted(j.title for j in jobs) == ["Customer Success Manager", "Sales Associate"]  # engineer out, duplicate out
    assert sorted(c for c, _ in found) == ["Acme", "Zed"]


def test_discovery_uses_the_ats_link_in_the_ad_and_remembers_failures(monkeypatch):
    db = FakeDB()
    src = make_source(db, sites=["a"])
    job = RawJob(source="remotejobs", external_id="a:1", title="Account Executive", company="Linked Co",
                 apply_url="https://boards.greenhouse.io/linkedco/jobs/55")
    monkeypatch.setattr(src, "_fetch_site", lambda site: [job])
    monkeypatch.setattr(remote, "discover_board", lambda *a: pytest.fail("no guessing when the ad links the ATS"))
    src.fetch()
    assert db.added and db.added[0][:2] == ("greenhouse", "linkedco") and db.added[0][4]["remote_first"]
    # a company without a board is not probed again on the next run
    calls = []
    job2 = RawJob(source="remotejobs", external_id="a:2", title="Sales Associate", company="Ghost Co", apply_url="https://x/2")
    monkeypatch.setattr(src, "_fetch_site", lambda site: [job2])
    monkeypatch.setattr(remote, "discover_board", lambda comp, *a: calls.append(comp) or None)
    db.kv.pop("remote_last:a", None)
    src.fetch()
    db.kv.pop("remote_last:a", None)
    src.fetch()
    assert calls == ["Ghost Co"]


def test_a_failing_site_does_not_stop_the_others(monkeypatch):
    src = make_source(FakeDB(), sites=["a", "b"])

    def fetch_site(site):
        if site == "a":
            raise RuntimeError("down")
        return [RawJob(source="remotejobs", external_id="b:1", title="Sales Associate", company="Zed")]
    monkeypatch.setattr(src, "_fetch_site", fetch_site)
    monkeypatch.setattr(remote, "discover_board", lambda *a: None)
    assert [j.company for j in src.fetch()] == ["Zed"]


def test_all_sites_failing_raises_and_nothing_due_is_quiet(monkeypatch):
    src = make_source(FakeDB(), sites=["a"])
    monkeypatch.setattr(src, "_fetch_site", lambda site: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        src.fetch()
    db = FakeDB()
    db.kv["remote_last:a"] = __import__("time").time()
    quiet = make_source(db, sites=["a"])
    monkeypatch.setattr(quiet, "_fetch_site", lambda site: pytest.fail("not due"))
    assert quiet.fetch() == []


# ------------------------------------------------------------------ AI stop factor: remote, but only for another country
def test_not_vienna_blocker_covers_country_restricted_remote_jobs():
    from datetime import date
    from jobradar import ai

    def blockers(location, desc, quote):
        job = {"id": 1, "title": "Customer Success Manager", "company": "X", "location": location,
               "description": desc, "meta": {}}
        r = {"blockers": ["not_vienna"], "hours_ok": "yes", "risks": [],
             "evidence": [{"blocker": "not_vienna", "quote": quote}]}
        ai.verify_blockers(r, job, date(2026, 11, 8))
        return r["blockers"]

    us = "This role is remote. Candidates must be located in the United States and authorized to work in the US."
    assert blockers("Remote (Worldwide)", us, "Candidates must be located in the United States") == ["not_vienna"]
    assert blockers("Remote", "Location: Remote - US", "Remote - US") == ["not_vienna"]
    # Europe / worldwide / Austria in the quote → not a restriction for him
    assert blockers("Remote", "Remote, Europe or US time zones", "Remote, Europe or US time zones") == []
    assert blockers("Remote", "We hire worldwide, remote, anywhere", "We hire worldwide, remote, anywhere") == []
    # a plain remote quote with no country is no proof
    assert blockers("Remote", "Fully remote team with offices in the US", "Fully remote team") == []
    # a job located in Vienna can never be blocked
    assert blockers("Wien", "Standort Wien, remote moeglich USA", "remote moeglich USA") == []
