from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawJob:
    """A job posting as returned by a source, before any processing."""

    source: str  # e.g. "linkedin", "indeed", "karriere", "zbp", "greenhouse:bitpanda"
    external_id: str
    title: str
    company: str
    location: str = ""
    url: str = ""  # canonical posting URL on the source
    apply_url: str = ""  # best known direct application URL (company site / ATS)
    description: str = ""  # plain text
    posted_at: datetime | None = None
    company_linkedin: str = ""  # e.g. https://www.linkedin.com/company/bitpanda
    meta: dict[str, Any] = field(default_factory=dict)
    needs_detail: bool = False  # description must be fetched with source.fetch_detail()

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def source_family(self) -> str:
        return self.source.split(":", 1)[0]
