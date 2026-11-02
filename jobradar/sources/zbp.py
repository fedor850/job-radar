"""WU ZBP Career Center job board (my.zbp.at) — student jobs, internships, trainee programmes for WU students."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text
from .base import Source

LIST = "https://my.zbp.at/de/jobs/alle-jobs-anzeigen/"
BASE = "https://my.zbp.at"


class ZbpSource(Source):
    name = "zbp"
    label = "WU ZBP Career Center"

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        self.begin()
        out: dict[str, RawJob] = {}
        try:
            r = http.get(self.params.get("url", LIST))
            self._parse(r.text, out)
        except Exception as e:  # noqa: BLE001 - the browser below can still read the list
            self.note_error(e, "plain list page")
        if not out and self.params.get("browser", True):
            # since ~25.10.2026 my.zbp.at renders the job list client-side (Blazor Server): plain HTML has no
            # job links → read the list with headless Chromium (detail pages are still server-rendered)
            pages: list[str] = []
            for attempt in (1, 2):
                try:
                    self._browser_pages(int(self.params.get("browser_pages", 10 if backfill else 5)), pages)
                    break
                except Exception as e:  # noqa: BLE001 - Chromium crashes / the Blazor circuit stalls now and then
                    self.note_error(e, f"browser attempt {attempt}")
                    if pages:  # got at least the first page(s): good enough, do not start over
                        break
                    time.sleep(5)
            for html in pages:
                self._parse(html, out)
        return self.finish(list(out.values()))

    @staticmethod
    def _first_link(pg) -> str | None:
        """href of the first job link, or None while the list is being redrawn (eval_on_selector would raise)."""
        return pg.evaluate("() => { const e = document.querySelector(\"a[href*='/jobs/detail']\"); "
                           "return e ? e.href : null; }")

    def _browser_pages(self, max_pages: int, pages: list[str]) -> None:
        """Read up to `max_pages` list pages with headless Chromium; pages are appended to `pages` as they are
        read, so an error on page 3 does not lose pages 1–2."""
        from playwright.sync_api import sync_playwright  # heavy, optional

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                pg = browser.new_page(locale="de-AT")
                # not "networkidle": the Blazor circuit keeps a websocket open and the page never becomes idle
                pg.goto(self.params.get("browser_url", "https://my.zbp.at/de/jobs/"), timeout=60000,
                        wait_until="domcontentloaded")
                for n in range(1, max_pages + 1):
                    pg.wait_for_selector("a[href*='/jobs/detail']", timeout=45000)
                    pages.append(pg.content())
                    nxt = pg.locator("button", has_text=re.compile(rf"^\s*{n + 1}\s*$"))
                    if nxt.count() == 0:
                        break
                    first = self._first_link(pg)
                    nxt.first.click()
                    for _ in range(30):  # wait (≤ 15 s) until the list actually shows the next page
                        pg.wait_for_timeout(500)
                        cur = self._first_link(pg)
                        if cur and cur != first:
                            break
                    else:
                        break
            finally:
                browser.close()

    def _parse(self, html: str, out: dict[str, RawJob]) -> None:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/jobs/detail/']"):
            href = urljoin(BASE, a.get("href", ""))
            m = re.search(r"id=([0-9a-f\-]{20,})", href)
            if not m:
                continue
            jid = m.group(1)
            if jid in out:
                continue
            card = a
            for _ in range(4):
                if card.parent is not None:
                    card = card.parent
            parts = [p for p in card.get_text("|", strip=True).split("|") if p.strip()]
            title = a.get_text(" ", strip=True) or (parts[0] if parts else "")
            company = ""
            posted = None
            for p in parts:
                if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", p.strip()):
                    try:
                        posted = datetime.strptime(p.strip(), "%d.%m.%Y").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
                elif p.strip() != title and p.strip() not in title and not company:
                    company = p.strip()
            out[jid] = RawJob(
                source="zbp",
                external_id=jid,
                title=title,
                company=company,
                location="",
                url=href,
                posted_at=posted,
                needs_detail=True,
            )

    def fetch_detail(self, job: RawJob) -> RawJob:
        r = http.get(job.url)
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        main = soup.select_one("main") or soup.body
        text = html_to_text(str(main)) if main else ""
        # cut the PDF-viewer noise that follows the description
        for marker in ("\nof\n0\n100%", "Rotate Clockwise"):
            if marker in text:
                text = text.split(marker, 1)[0] + "\n" + text.split("Publikationsdatum", 1)[-1][:600]
                break
        job.description = text.strip()
        # header block: "• Company / • 50% / • Studienbegleitend (Teilzeit) / • Wien" before "Beschreibung"
        head = text.split("Beschreibung", 1)[0]
        bullets = [ln[2:].strip() for ln in head.splitlines() if ln.startswith("• ")]
        if bullets:
            if not job.company:
                job.company = bullets[0]
            if len(bullets) >= 2:
                job.location = bullets[-1]
                job.meta["workload"] = " · ".join(bullets[1:-1])
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            label = a.get_text(" ", strip=True).lower()
            if href.startswith("http") and "zbp.at" not in href and any(
                k in label for k in ("bewerb", "apply", "hier", "jetzt")
            ):
                job.apply_url = href
                break
        job.needs_detail = False
        return job
