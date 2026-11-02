"""LinkedIn guest search: one request at a time, retry before pausing, page step 10 (Nov 2026)."""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar import http  # noqa: E402
from jobradar.models import RawJob  # noqa: E402
from jobradar.sources import linkedin  # noqa: E402
from jobradar.sources.linkedin import LinkedInBlocked, LinkedInSource  # noqa: E402

CARD = (
    '<li><div data-entity-urn="urn:li:jobPosting:{id}"><a class="base-card__full-link" href="/jobs/view/x-{id}">'
    '</a><h3 class="base-search-card__title">Intern {id}</h3><h4 class="base-search-card__subtitle">ACME</h4>'
    '<span class="job-search-card__location">Vienna</span><time>1 hour ago</time></div></li>'
)


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Class-level state is shared by every run of the bot → reset it for each test; never really sleep."""
    LinkedInSource.cooldown_until = 0.0
    LinkedInSource._strikes = 0
    LinkedInSource._last_throttle = 0.0
    LinkedInSource._next_ok = 0.0
    LinkedInSource._rr = 0
    LinkedInSource._day_runs = 0
    sleeps: list[float] = []
    monkeypatch.setattr(linkedin.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def make(**params) -> LinkedInSource:
    return LinkedInSource(None, params)


def script(monkeypatch, outcomes):
    """Replace http.get: `outcomes` is a list of status codes / Resp objects, used in order (the last repeats)."""
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        o = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(o, int):
            if o in kw.get("ok", (200,)):
                return Resp(o, "")
            raise http.HttpError(o, url)
        return o

    monkeypatch.setattr(linkedin.http, "get", fake_get)
    return calls


def test_single_429_is_retried_without_pause(monkeypatch, fresh_state):
    calls = script(monkeypatch, [429, Resp(200, "ok")])
    r = make()._get("https://x/search")
    assert r.text == "ok" and len(calls) == 2
    assert LinkedInSource.cooldown_until == 0 and LinkedInSource._strikes == 0  # no strike for a retried 429
    assert any(25 <= s <= 50 for s in fresh_state)  # waited 25–50 s before the retry


def test_gives_up_after_retries_and_pauses_once(monkeypatch):
    calls = script(monkeypatch, [429])
    src = make()
    with pytest.raises(LinkedInBlocked):
        src._get("https://x/search")
    assert len(calls) == 3  # 1 + 2 retries
    assert LinkedInSource._strikes == 1 and LinkedInSource.cooldown_until > time.time()
    assert LinkedInSource.cooldown_until - time.time() <= 600  # first pause is 10 min
    with pytest.raises(LinkedInBlocked):  # during the pause nothing is requested and no new strike is counted
        src._get("https://x/search")
    assert len(calls) == 3 and LinkedInSource._strikes == 1


def test_pause_ladder_is_10_20_30(monkeypatch):
    script(monkeypatch, [429])
    src = make()
    pauses = []
    for _ in range(4):
        LinkedInSource.cooldown_until = 0.0  # the previous pause is over
        with pytest.raises(LinkedInBlocked):
            src._get("https://x/search")
        pauses.append(round((LinkedInSource.cooldown_until - time.time()) / 60))
    assert pauses == [10, 20, 30, 30]


def test_other_errors_are_not_throttling(monkeypatch):
    calls = script(monkeypatch, [404])
    with pytest.raises(http.HttpError) as e:
        make()._get("https://x/detail")
    assert e.value.status == 404 and len(calls) == 1 and LinkedInSource.cooldown_until == 0


def test_400_page_is_accepted_for_search(monkeypatch):
    script(monkeypatch, [400])
    r = make()._get("https://x/search", ok=(200, 400))
    assert r.status_code == 400


def test_page_step_is_10_and_stops_on_empty_page(monkeypatch):
    pages = {0: [str(i) for i in range(10)], 10: [str(i) for i in range(10, 20)], 20: ["20", "21"]}
    urls: list[str] = []

    def fake_cards(self, url):
        urls.append(url)
        start = int(url.split("start=")[1].split("&")[0])
        return [RawJob("linkedin", i, f"Intern {i}", "ACME") for i in pages.get(start, [])]

    monkeypatch.setattr(LinkedInSource, "_get_cards", fake_cards)
    out: dict = {}
    added = make()._run_query({"keywords": "", "location": "Vienna, Vienna, Austria", "distance": 25}, 7200, 6, out)
    assert [int(u.split("start=")[1].split("&")[0]) for u in urls] == [0, 10, 20, 30]
    assert added == 22 and len(out) == 22 and "f_TPR=r7200" in urls[0] and "distance=25" in urls[0]


def test_page_size_is_configurable(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(LinkedInSource, "_get_cards", lambda self, u: urls.append(u) or [])
    make(page_size=25)._run_query({"keywords": "x"}, 3600, 3, {})
    assert "start=0" in urls[0] and len(urls) == 1  # empty first page → stop


def test_cards_are_parsed_from_a_search_page(monkeypatch):
    script(monkeypatch, [Resp(200, "<ul>" + CARD.format(id="4400000001") + CARD.format(id="4400000002") + "</ul>")])
    cards = make()._get_cards("https://x/search?start=0")
    assert [c.external_id for c in cards] == ["4400000001", "4400000002"]
    assert cards[0].url.endswith("/4400000001/") and cards[0].needs_detail


def test_fetch_counts_one_strike_per_give_up(monkeypatch):
    script(monkeypatch, [429])
    src = make(queries=[{"keywords": "", "location": "Vienna"}])
    with pytest.raises(LinkedInBlocked):
        src.fetch()
    assert LinkedInSource._strikes == 1  # used to be counted twice (once in the request, once in fetch)
    assert src.fetch() == []  # during the pause: nothing requested, nothing raised


def test_fetch_keeps_cards_found_before_the_block(monkeypatch):
    page0 = Resp(200, "<ul>" + CARD.format(id="4400000001") + "</ul>")
    script(monkeypatch, [page0, 429])  # page 0 ok, page 1 always 429
    src = make(queries=[{"keywords": "", "location": "Vienna"}])
    got = src.fetch()
    assert [j.external_id for j in got] == ["4400000001"] and LinkedInSource._strikes == 1


def test_one_request_at_a_time_across_threads(monkeypatch):
    """Searches and job pages run in different worker threads: they must queue up, never overlap."""
    monkeypatch.undo()  # real sleep for this test
    state = {"active": 0, "max": 0, "n": 0}
    guard = threading.Lock()

    def fake_get(url, **kw):
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            state["n"] += 1
        time.sleep(0.01)
        with guard:
            state["active"] -= 1
        return Resp(200, "ok")

    monkeypatch.setattr(linkedin.http, "get", fake_get)
    src = make(delay_sec=(0.01, 0.02))
    threads = [threading.Thread(target=lambda: [src._get("https://x/detail") for _ in range(3)]) for _ in range(5)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert state["n"] == 15 and state["max"] == 1


def test_requests_are_spaced(monkeypatch, fresh_state):
    script(monkeypatch, [Resp(200, "ok")])
    src = make(delay_sec=(3.0, 5.5))
    src._get("https://x/1")
    assert LinkedInSource._next_ok - time.time() > 2.5  # the next request waits 3–5.5 s
    src._get("https://x/2")
    assert any(2.5 < s <= 5.5 for s in fresh_state)


# ------------------------------------------------------------------ shared http helper: one patient 429 retry
class FakeSession:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, 0

    def request(self, method, url, timeout=None, **kw):
        self.calls += 1
        return self.outcomes[min(self.calls, len(self.outcomes)) - 1]


def test_http_retries_a_429_once(monkeypatch):
    sleeps = []
    s = FakeSession([Resp(429, "slow down"), Resp(200, "fine")])
    monkeypatch.setattr(http, "session", lambda: s)
    monkeypatch.setattr(http.time, "sleep", lambda x: sleeps.append(x))
    assert http.get("https://x").text == "fine" and s.calls == 2 and 8 <= sleeps[0] <= 15


def test_http_honours_short_retry_after_and_skips_long_one(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", lambda x: sleeps.append(x))
    s = FakeSession([Resp(429, "", {"Retry-After": "5"}), Resp(200, "fine")])
    monkeypatch.setattr(http, "session", lambda: s)
    assert http.get("https://x").text == "fine" and 6 <= sleeps[0] <= 8
    s2 = FakeSession([Resp(429, "", {"Retry-After": "3600"}), Resp(200, "fine")])
    monkeypatch.setattr(http, "session", lambda: s2)
    with pytest.raises(http.HttpError) as e:  # asked to wait an hour → give up at once, do not hammer
        http.get("https://x")
    assert e.value.status == 429 and s2.calls == 1


def test_http_second_429_raises_and_retry_can_be_disabled(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda x: None)
    s = FakeSession([Resp(429)])
    monkeypatch.setattr(http, "session", lambda: s)
    with pytest.raises(http.HttpError):
        http.get("https://x")
    assert s.calls == 2  # 1 + 1 retry
    s0 = FakeSession([Resp(429)])
    monkeypatch.setattr(http, "session", lambda: s0)
    with pytest.raises(http.HttpError):
        http.get("https://x", retry_429=0)
    assert s0.calls == 1


def test_http_429_in_ok_is_returned_as_is(monkeypatch):
    s = FakeSession([Resp(429, "x")])
    monkeypatch.setattr(http, "session", lambda: s)
    assert http.get("https://x", ok=(200, 429)).status_code == 429 and s.calls == 1
