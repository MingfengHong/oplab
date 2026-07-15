from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PassageCandidate:
    locator: str
    text: str
    start_char: int | None = None
    end_char: int | None = None


@dataclass(frozen=True)
class SourceCandidate:
    source_type: str
    title: str
    uri: str
    content: str
    passages: list[PassageCandidate]
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    published_at: str | None = None
    quality: dict = field(default_factory=dict)
    license_status: str = "metadata_only"


class SearchAdapter(Protocol):
    async def search(self, query: str, limit: int) -> list[SourceCandidate]: ...
