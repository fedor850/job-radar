from __future__ import annotations

import logging
from typing import Any

from ..models import RawJob


class Source:
    """A job source. `fetch()` and `fetch_detail()` are blocking and run in a worker thread."""

    name: str = "base"
    #: label shown to the user, e.g. "LinkedIn"
    label: str = "base"

    def __init__(self, settings: Any, params: dict | None = None):
        self.settings = settings
        self.params = params or {}
        self.log = logging.getLogger(f"jobradar.source.{self.name}")

    @property
    def interval(self) -> int:
        return int(self.params.get("interval_sec", 300))

    @property
    def enabled(self) -> bool:
        return bool(self.params.get("enabled", True))

    def fetch(self, backfill: bool = False) -> list[RawJob]:
        raise NotImplementedError

    # ---- partial results: one failed page must not throw away everything fetched before it
    errors: list[Exception]

    def begin(self) -> None:
        """Call at the start of fetch(): forget the errors of the previous run."""
        self.errors = []

    def note_error(self, e: Exception, what: str = "") -> None:
        """Remember an error of ONE request and carry on with the rest of the run."""
        if not hasattr(self, "errors"):
            self.errors = []
        self.errors.append(e)
        self.log.warning("%s: %s failed (%s: %s) — keeping what was fetched", self.name, what or "request",
                         type(e).__name__, e)

    def finish(self, out: list[RawJob]) -> list[RawJob]:
        """Return the jobs; raise the first error only if the run produced nothing at all (a real outage)."""
        errs = getattr(self, "errors", [])
        if errs and not out:
            raise errs[0]
        return out

    def fetch_detail(self, job: RawJob) -> RawJob:
        return job
