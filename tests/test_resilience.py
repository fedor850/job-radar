"""Source resilience (Nov 2026): one-off failures must not lose jobs.

Covers the shared HTTP layer (network errors, 5xx, random 403/202, empty JSON), "keep what was fetched" in the sources,
sites that must not forget an ad after a timeout, the JOIN layout change, ZBP's browser fallback and the back-off
for jobs whose page could not be opened."""
import asyncio
import os
import sys
from datetime import timedelta

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar import http  # noqa: E402
from jobradar.db import DB  # noqa: E402
from jobradar.models import RawJob  # noqa: E402
from jobradar.pipeline import Pipeline  # noqa: E402
from jobradar.sources import boards_extra, karriere, willhaben, zbp  # noqa: E402
from jobradar.sources.base import Source  # noqa: E402
from jobradar.sources.sites import SiteSource  # noqa: E402
from jobradar.util import iso, now_utc  # noqa: E402


class Resp:
    def __init__(self, status=200, text="", headers=None, payload=None):
        self.status_code, self.text, self.headers, self._p = status, text, headers or {}, payload
        self.encoding = "utf-8"

    def json(self):
        if self._p is not None:
            return self._p
        import json
        return json.loads(self.text)


class Seq:
    """Fake requests.Session: a list of outcomes (Resp or exception) used in order; the last one repeats."""

    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, 0

    def request(self, method, url, timeout=None, **kw):
        o = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    return sleeps


def use(monkeypatch, outcomes):
    s = Seq(outcomes)
    monkeypatch.setattr(http, "session", lambda: s)
    return s


# ------------------------------------------------------------------------------------------------ http layer
def test_connection_error_is_retried_once(monkeypatch):
    s = use(monkeypatch, [requests.exceptions.ReadTimeout("slow"), Resp(200, "ok")])
    assert http.get("https://x").text == "ok" and s.calls == 2


def test_half_read_body_is_retried(monkeypatch):
    s = use(monkeypatch, [requests.exceptions.ChunkedEncodingError("IncompleteRead"), Resp(200, "ok")])
    assert http.get("https://x").text == "ok" and s.calls == 2


def test_network_error_twice_raises(monkeypatch):
    s = use(monkeypatch, [requests.exceptions.ConnectionError("down")])
    with pytest.raises(requests.exceptions.ConnectionError):
        http.get("https://x")
    assert s.calls == 2
    s2 = use(monkeypatch, [requests.exceptions.ConnectionError("down")])
    with pytest.raises(requests.exceptions.ConnectionError):
        http.get("https://x", retry_net=0)
    assert s2.calls == 1


def test_5xx_retried_once_then_raised(monkeypatch):
    s = use(monkeypatch, [Resp(503), Resp(200, "ok")])
    assert http.get("https://x").text == "ok" and s.calls == 2
    s2 = use(monkeypatch, [Resp(502)])
    with pytest.raises(http.HttpError) as e:
        http.get("https://x")
    assert e.value.status == 502 and s2.calls == 2


def test_404_is_never_retried(monkeypatch):
    s = use(monkeypatch, [Resp(404)])
    with pytest.raises(http.HttpError):
        http.get("https://x")
    assert s.calls == 1


def test_random_403_is_retried_with_a_fresh_session(monkeypatch):
    resets = []
    monkeypatch.setattr(http, "reset_session", lambda: resets.append(1))
    s = use(monkeypatch, [Resp(403), Resp(200, "ok")])
    assert http.get("https://x", retry_block=(403,)).text == "ok" and s.calls == 2 and resets == [1]
    s2 = use(monkeypatch, [Resp(403)])
    with pytest.raises(http.HttpError):  # a permanent block: one retry only, then the error
        http.get("https://x", retry_block=(403,))
    assert s2.calls == 2
    s3 = use(monkeypatch, [Resp(403)])
    with pytest.raises(http.HttpError):  # default: no retry for 403
        http.get("https://x")
    assert s3.calls == 1


def test_202_challenge_retry_and_ok_wins(monkeypatch):
    s = use(monkeypatch, [Resp(202), Resp(200, "page")])
    assert http.get("https://x", ok=(200, 404), retry_block=(202, 403)).text == "page" and s.calls == 2
    s2 = use(monkeypatch, [Resp(404)])
    assert http.get("https://x", ok=(200, 404), retry_block=(202, 403)).status_code == 404 and s2.calls == 1


def test_empty_json_body_is_fetched_again(monkeypatch):
    s = use(monkeypatch, [Resp(200, ""), Resp(200, '{"a": 1}')])
    assert http.get_json("https://x") == {"a": 1} and s.calls == 2
    s2 = use(monkeypatch, [Resp(200, "<html>")])
    with pytest.raises(ValueError):
        http.post_json("https://x", {})
    assert s2.calls == 2


def test_reset_session_gives_a_new_session():
    a = http.session()
    http.reset_session()
    assert http.session() is not a


# ------------------------------------------------------------------------------------------------ base helper
class Dummy(Source):
    name = "dummy"


def test_finish_raises_only_when_nothing_was_fetched():
    d = Dummy(None)
    d.begin()
    d.note_error(RuntimeError("boom"), "page 2")
    job = RawJob("dummy", "1", "Intern", "ACME")
    assert d.finish([job]) == [job]
    with pytest.raises(RuntimeError):
        d.finish([])
    d.begin()  # a new run forgets the old errors
    assert d.finish([]) == []


# ------------------------------------------------------------------------------------------------ sources
def test_karriere_keeps_page_1_when_page_2_fails(monkeypatch):
    item = {"jobsItem": {"id": 7, "title": "Intern", "company": {"name": "ACME"}, "locations": [], "link": "u"}}
    page1 = {"data": {"jobsSearchList": {"activeItems": {"items": [item]}}}}

    def fake(url, **kw):
        if "page=1" in url:
            return page1
        raise http.HttpError(403, url)

    monkeypatch.setattr(karriere.http, "get_json", fake)
    got = karriere.KarriereSource(None, {"pages": 3}).fetch()
    assert [j.external_id for j in got] == ["7"]


def test_karriere_first_page_failure_is_an_error(monkeypatch):
    def fake(url, **kw):
        raise http.HttpError(503, url)

    monkeypatch.setattr(karriere.http, "get_json", fake)
    with pytest.raises(http.HttpError):
        karriere.KarriereSource(None, {"pages": 3}).fetch()


def test_willhaben_keeps_earlier_pages_and_retries_403(monkeypatch):
    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"jobsSearchResultRoot":'
            '{"data":{"entries":[{"id":"5","title":"Werkstudent","company":{"title":"ACME"}}]}}}}}</script>')
    seen = []

    def fake_get(url, **kw):
        seen.append(kw.get("retry_block"))
        if "page=1" in url:
            return Resp(200, html)
        raise http.HttpError(403, url)

    monkeypatch.setattr(willhaben.http, "get", fake_get)
    got = willhaben.WillhabenSource(None, {"pages": 3}).fetch()
    assert [j.external_id for j in got] == ["5"] and all(b == (403,) for b in seen)


LIST_HTML = '<a href="/jobs/ok-100000">a</a><a href="/jobs/bad-200000">b</a>'
SITE = {"name": "xingtest", "urls": ["https://x/list?p={page}"], "pages": 2,
        "link": r'href="(?P<url>/jobs/[a-z]+-(?P<id>\d{6}))"', "detail": "https://x{url}", "vienna_only": False}


def site(monkeypatch, getter, db=None):
    monkeypatch.setattr("jobradar.sources.sites.http.get", getter)
    return SiteSource(None, dict(SITE), db=db)


def test_site_list_page_failure_does_not_lose_the_other_page(monkeypatch):
    def getter(url, **kw):
        if "p=1" in url:
            raise http.HttpError(202, url)
        if "/list" in url:
            return Resp(200, LIST_HTML)
        return Resp(200, "<html><h1>Title</h1><main>" + "text " * 100 + "</main></html>")

    jobs = site(monkeypatch, getter).fetch()
    assert {j.external_id for j in jobs} == {"100000", "200000"} and not any(j.meta.get("drop") for j in jobs)


def test_site_all_list_pages_failing_raises(monkeypatch):
    def getter(url, **kw):
        raise http.HttpError(202, url)

    with pytest.raises(http.HttpError):
        site(monkeypatch, getter).fetch()


def test_site_timeout_on_detail_is_not_remembered_as_dropped(monkeypatch):
    """A timeout/429 on the ad page used to be stored as 'dropped' → the ad was lost for good."""
    def getter(url, **kw):
        if "/list" in url:
            return Resp(200, LIST_HTML)
        raise requests.exceptions.ReadTimeout("slow")

    jobs = site(monkeypatch, getter).fetch()
    assert jobs == []  # nothing returned → nothing stored → fetched again on the next run


def test_site_404_on_detail_is_dropped(monkeypatch):
    def getter(url, **kw):
        if "/list" in url:
            return Resp(200, '<a href="/jobs/gone-300000">a</a>')
        return Resp(404)

    jobs = site(monkeypatch, getter).fetch()
    assert len(jobs) == 1 and jobs[0].meta.get("drop")


def test_site_stops_after_five_transient_detail_failures(monkeypatch):
    calls = []
    links = "".join(f'<a href="/jobs/x-{100000 + i}">a</a>' for i in range(20))

    def getter(url, **kw):
        if "/list" in url:
            return Resp(200, links)
        calls.append(url)
        raise http.HttpError(429, url)

    site(monkeypatch, getter).fetch()
    assert len(calls) == 5


# ------------------------------------------------------------------------------------------------ JOIN
def test_join_company_old_and_new_layout():
    new = {"props": {"pageProps": {"pageData": {"company": {"id": 34013, "domain": "karrierearchitekten"}}}}}
    old = {"props": {"pageProps": {"initialState": {"company": {"id": 5, "domain": "acme"}}}}}
    assert boards_extra._join_company(new)["id"] == 34013
    assert boards_extra._join_company(old)["id"] == 5
    assert boards_extra._join_company({"props": {"pageProps": {"locale": "en"}}}) == {}


def test_join_board_works_with_the_new_page_layout(monkeypatch):
    boards_extra._JOIN_IDS.clear()
    page = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"pageData":'
            '{"company":{"id":34013,"domain":"karrierearchitekten"}}}}}</script>')
    items = {"items": [{"id": 9, "idParam": "9-sales", "title": "Sales Intern", "city": {"cityName": "Wien"},
                        "country": {"name": "Austria"}, "createdAt": "2026-11-01T10:00:00Z"}],
             "pagination": {"pageCount": 1}}
    monkeypatch.setattr(boards_extra.http, "get", lambda url, **kw: Resp(200, page))
    monkeypatch.setattr(boards_extra.http, "get_json", lambda url, **kw: items)
    got = boards_extra.join("karrierearchitekten", {}, "Die Karrierearchitekten")
    assert [j.external_id for j in got] == ["9"] and boards_extra._JOIN_IDS["karrierearchitekten"][0] == 34013
    monkeypatch.setattr(boards_extra.http, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached id")))
    assert boards_extra.join("karrierearchitekten", {}, "x")  # second run does not load the company page again


# ------------------------------------------------------------------------------------------------ ZBP
CARD = '<div><div><div><div><a href="/de/jobs/detail/?id={id}">Werkstudent</a><span>ACME</span></div></div></div></div>'
JOB_ID = "0123456789abcdef0123456789abcdef"


def zbp_source(monkeypatch, browser):
    monkeypatch.setattr(zbp.http, "get", lambda url, **kw: Resp(200, "<html>no jobs rendered</html>"))
    s = zbp.ZbpSource(None, {})
    monkeypatch.setattr(zbp.ZbpSource, "_browser_pages", browser)
    return s


def test_zbp_browser_is_retried_after_a_crash(monkeypatch):
    attempts = []

    def browser(self, max_pages, pages):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        pages.append(CARD.format(id=JOB_ID))

    got = zbp_source(monkeypatch, browser).fetch()
    assert len(attempts) == 2 and [j.external_id for j in got] == [JOB_ID]


def test_zbp_keeps_pages_read_before_an_error(monkeypatch):
    attempts = []

    def browser(self, max_pages, pages):
        attempts.append(1)
        pages.append(CARD.format(id=JOB_ID))
        raise RuntimeError("page 2 never loaded")

    got = zbp_source(monkeypatch, browser).fetch()
    assert len(attempts) == 1 and len(got) == 1


def test_zbp_two_failures_without_pages_raise(monkeypatch):
    def browser(self, max_pages, pages):
        raise RuntimeError("no list")

    with pytest.raises(RuntimeError):
        zbp_source(monkeypatch, browser).fetch()


# ------------------------------------------------------------------------------------------------ retry back-off
@pytest.fixture
def pipe(tmp_path):
    db = DB(tmp_path / "t.sqlite3")
    p = Pipeline.__new__(Pipeline)
    p.db = db
    p.sources = {"karriere": object()}
    p.evaluated = []

    async def evaluate(job_id, raw, name, silent=False, backfill=False):
        p.evaluated.append(job_id)
        db.update_job(job_id, stage="error", reason="HttpError: HTTP 503")  # still failing
        return "error"

    p.evaluate = evaluate
    return p


def error_job(db, minutes_ago, attempts):
    raw = RawJob("karriere", f"id{minutes_ago}-{attempts}", "Intern", "ACME", url="u")
    jid = db.insert_job(raw, f"fp{minutes_ago}{attempts}", stage="error", reason="HttpError: HTTP 503")
    meta = {"attempts": attempts}
    db.update_job(jid, meta=meta)
    db.execute("UPDATE jobs SET updated=? WHERE id=?", (iso(now_utc() - timedelta(minutes=minutes_ago)), jid))
    return jid


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_failed_page_is_retried_with_growing_gaps(pipe):
    due = error_job(pipe.db, 30, 2)  # 3rd attempt needs a 10-min gap → due
    early = error_job(pipe.db, 12, 4)  # 5th attempt needs 40 min → not yet
    run(pipe.retry_errors())
    assert pipe.evaluated == [due]
    assert pipe.db.get_job(early)["stage"] == "error"  # waiting, no attempt spent
    assert (pipe.db.get_job(early)["meta"] or {}).get("attempts") == 4


def test_job_survives_hours_of_outage_but_is_finally_written_off(pipe):
    survivor = error_job(pipe.db, 300, 5)  # 6th attempt after 5 h of trouble: still tried
    dead = error_job(pipe.db, 300, 8)  # 9th attempt: written off
    run(pipe.retry_errors())
    assert pipe.evaluated == [survivor] and pipe.db.get_job(dead)["stage"] == "failed"


def test_waiting_jobs_do_not_block_due_ones(pipe):
    for i in range(60):  # 60 jobs still in back-off must not use up the batch
        error_job(pipe.db, 6 + i % 3, 6)
    due = error_job(pipe.db, 400, 1)
    run(pipe.retry_errors())
    assert due in pipe.evaluated


# ------------------------------------------------------------------------------------------------ broken-site alert
def test_failing_boards_query(tmp_path):
    db = DB(tmp_path / "b.sqlite3")
    db.upsert_board("join", "dead", "Dead Co", "seed", {})
    db.upsert_board("join", "fine", "Fine Co", "seed", {})
    bid = db.one("SELECT id FROM boards WHERE key='dead'")["id"]
    for _ in range(31):
        db.board_result(bid, False, "KeyError: 'x'")
    assert [b["key"] for b in db.failing_boards(30)] == ["dead"]
    db.board_result(bid, True)
    assert db.failing_boards(30) == []
