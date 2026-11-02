"""DER STANDARD Jobs (jobs.derstandard.at) — big Austrian board, strong for banks, public bodies (OeNB, FMA) and
international employers in Vienna. Server-rendered result cards; full text on the detail page."""
from __future__ import annotations

import random
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..models import RawJob
from ..util import html_to_text, parse_relative_age
from .base import Source

BASE = "https://jobs.derstandard.at"
HDR = {"Accept": "text/html", "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}
DEFAULT_KEYWORDS = ["praktikum", "praktikant", "student", "werkstudent", "trainee", "junior", "intern", "teilzeit",
                    "english", "assistenz", "marketing", "vertrieb", "controlling", "finanz", "consulting", "analyst",
                    "sommerpraktikum", "summer internship", "ferialpraktikum"]
_UUID = re.compile(r"/detail/([0-9a-f\-]{36})")


class DerStandardSource(Source):
    name = "derstandard"
    label = "DER STANDARD Jobs"

    def _cards(self, url: str, out: dict[str, RawJob]) -> int:
        try:
            r = http.get(url, headers=HDR, ok=(200, 404), retry_block=(403,))
        except Exception as e:  # noqa: BLE001 - one keyword page failing must not lose the others
            self.note_error(e, url)
            return 0
        if r.status_code != 200:
            return 0
        soup = BeautifulSoup(r.text, "lxml")
        n = 0
        for a in soup.select("a[href*='/detail/']"):
            m = _UUID.search(a.get("href", ""))
            if not m or m.group(1) in out:
                continue
            card = a
            for _ in range(4):
                if card.parent is not None:
                    card = card.parent
            parts = [p.strip(" |\xa0") for p in card.get_text("\x1f", strip=True).split("\x1f")]
            parts = [p for p in parts if p and p != "JOB-TIPP!"]
            if len(parts) < 2 or parts[0].lower() in ("easyapply", "neu"):
                continue
            title = parts[0]
            company = parts[1] if len(parts) > 1 else ""
            age = next((p for p in parts if re.search(r"vor \d+|heute|gestern|gerade eben", p, re.I)), "")
            if re.search(r"gerade eben", age, re.I):
                age = "vor 1 Minute"
            loc = next((p for p in parts[2:] if re.search(r"\b\d{4}\b.*wien|wien|home-office", p, re.I)), "")
            out[m.group(1)] = RawJob(
                source="derstandard", external_id=m.group(1), title=title, company=company,
                location=loc or "Wien", url=urljoin(BASE, a["href"]), posted_at=parse_relative_age(age) if age else None,
                description=" · ".join(parts[:12]), needs_detail=True,
            )
            n += 1
        return n

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        loc = self.params.get("location_slug", "wien")
        out: dict[str, RawJob] = {}
        self.begin()
        self._cards(f"{BASE}/p/l/{loc}", out)
        for kw in self.params.get("keywords") or DEFAULT_KEYWORDS:
            time.sleep(random.uniform(0.8, 1.6))
            self._cards(f"{BASE}/p/ls/{loc}/{kw}", out)
        return self.finish(list(out.values()))

    def fetch_detail(self, job: RawJob) -> RawJob:
        r = http.get(job.url, headers=HDR, ok=(200, 404, 410))
        if r.status_code != 200:
            job.meta["drop"] = True
            job.needs_detail = False
            return job
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        main = soup.select_one("main") or soup.body
        text = html_to_text(str(main)) if main else ""
        if text:
            job.description = text[:12000]
        for a in soup.select("a[href^='http']"):
            href = a["href"]
            if "derstandard" not in href and re.search(r"bewerb|apply", a.get_text(" ", strip=True), re.I):
                job.apply_url = href
                break
        job.needs_detail = False
        return job
