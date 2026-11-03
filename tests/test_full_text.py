"""Full ad text (Nov 2026): never score a job on a list snippet, read ads hidden behind "show more"."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar import browser  # noqa: E402
from jobradar.db import DB  # noqa: E402
from jobradar.models import RawJob  # noqa: E402
from jobradar.pipeline import Pipeline  # noqa: E402
from jobradar.sources import boards, karriere, original  # noqa: E402
from jobradar.util import html_to_text, iso, now_utc  # noqa: E402

LONG = "Wir suchen eine Werkstudentin für Sales. " * 40


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_null_placeholder_lines_are_dropped():
    assert html_to_text("<p>Bewirb Dich jetzt.</p><p>null</p><p>null</p>") == "Bewirb Dich jetzt."
    assert "null" in html_to_text("<p>null hypothesis testing</p>")  # only whole lines


def test_karriere_detail_without_text_is_retried_not_scored_on_the_snippet(monkeypatch):
    monkeypatch.setattr(karriere.http, "get_json", lambda *a, **k: {"data": {"jobDetailContent": {}}})
    job = RawJob("karriere", "1", "Intern", "ACME", description="short list snippet", meta={"attempts": 1})
    with pytest.raises(RuntimeError):
        karriere.KarriereSource(None, {}).fetch_detail(job)
    job.meta["attempts"] = 5  # finally: take what there is
    assert karriere.KarriereSource(None, {}).fetch_detail(job).description == "short list snippet"


def test_retry_fetches_the_detail_again_even_if_a_snippet_is_stored(tmp_path):
    db = DB(tmp_path / "t.sqlite3")
    p = Pipeline.__new__(Pipeline)
    p.db, p.sources, seen = db, {"karriere": object()}, []

    async def evaluate(job_id, raw, name, silent=False, backfill=False):
        seen.append(raw.needs_detail)
        db.update_job(job_id, stage="blocked")
        return "blocked"

    p.evaluate = evaluate
    raw = RawJob("karriere", "9", "Intern", "ACME", url="u", description="snippet only", needs_detail=True)
    raw.meta["pending_detail"] = True
    jid = db.insert_job(raw, "fp9", stage="error", reason="HttpError: HTTP 503")
    db.execute("UPDATE jobs SET updated=? WHERE id=?", (iso(now_utc().replace(year=2020)), jid))
    run(p.retry_errors())
    assert seen == [True]


PERSONIO_XML = b"""<workzag-jobs><position><id>7</id><office>Wien</office><name>Sales Intern</name>
<jobDescriptions></jobDescriptions><schedule>part-time</schedule><createdAt>2026-11-01T10:00:00+00:00</createdAt>
</position></workzag-jobs>"""
PERSONIO_PAGE = ("<html><body><main><div class='jb-description-item'><h2>Aufgaben</h2><p>%s</p></div>"
                 "<div class='jb-description-item'><h2>Profil</h2><p>Du studierst.</p></div></main></body></html>" % LONG)


class R:
    def __init__(self, content=b"", text="", url="u"):
        self.content, self.text, self.url, self.encoding, self.status_code = content, text, url, "utf-8", 200


def test_personio_with_empty_feed_reads_the_posting_page(monkeypatch):
    monkeypatch.setattr(boards.http, "get", lambda url, **k: R(content=PERSONIO_XML))
    [job] = boards.personio("acme", {"tld": "de"}, "ACME")
    assert job.needs_detail and job.meta["board_kind"] == "personio"
    monkeypatch.setattr(boards.http, "get", lambda url, **k: R(text=PERSONIO_PAGE))
    job = boards.DETAILS["personio"](job)
    assert "Du studierst" in job.description and "Aufgaben" in job.description and not job.needs_detail


def test_fetch_posting_falls_back_to_the_browser(monkeypatch):
    shell = "<html><body><main>Loading…</main></body></html>"
    full = f"<html><body><main><h1>Sales Intern</h1><p>{LONG}</p></main></body></html>"
    monkeypatch.setattr(original.http, "get", lambda url, **k: R(text=shell))
    monkeypatch.setattr(browser, "render", lambda url, **k: {"url": url, "html": full, "text": "x", "clicked": 2})
    got = original.fetch_posting("https://acme.example/job/1", "Sales Intern")
    assert got and len(got["description"]) > 1000
    assert original.fetch_posting("https://acme.example/job/1", "Sales Intern", browser=False) is None


def test_browser_skips_sites_that_block_servers():
    for u in ("https://www.linkedin.com/jobs/view/1", "https://at.jooble.org/desc/1", "https://www.careerjet.at/jobad/x"):
        assert browser.render(u) is None


def test_cut_text_is_replaced_by_the_original(tmp_path, monkeypatch):
    db = DB(tmp_path / "c.sqlite3")
    p = Pipeline.__new__(Pipeline)
    p.db = db
    raw = RawJob("adzuna", "5", "Sales Intern", "ACME", url="u", description="Start of the ad only.")
    jid = db.insert_job(raw, "fp5", stage="queued")
    monkeypatch.setattr("jobradar.pipeline.find_original",
                        lambda db_, c, t: {"description": LONG, "via": "ats:personio", "url": "https://acme.jobs.personio.de/job/1"})
    job = run(p._complete_cut_text(jid, db.get_job(jid)))
    assert job["description"] == LONG and job["meta"]["full_text_from"] == "ats:personio"
    monkeypatch.setattr("jobradar.pipeline.find_original", lambda db_, c, t: None)
    raw2 = RawJob("adzuna", "6", "Other Intern", "ACME", url="u", description="Start of another ad only.")
    jid2 = db.insert_job(raw2, "fp6", stage="queued")
    assert run(p._complete_cut_text(jid2, db.get_job(jid2)))["meta"]["text_cut"] is True
