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

    def fetch_detail(self, job: RawJob) -> RawJob:
        return job
