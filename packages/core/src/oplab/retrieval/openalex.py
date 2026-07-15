from __future__ import annotations

from typing import Any

import httpx

from oplab.retrieval.base import SourceCandidate
from oplab.retrieval.text import split_passages


class OpenAlexSearch:
    endpoint = "https://api.openalex.org/works"

    def __init__(self, *, mailto: str | None = None, client: httpx.AsyncClient | None = None):
        self.mailto = mailto
        self.client = client

    async def search(self, query: str, limit: int) -> list[SourceCandidate]:
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=True)
        params: dict[str, Any] = {
            "search": query,
            "per-page": limit,
            "select": (
                "id,doi,title,publication_year,authorships,abstract_inverted_index,"
                "primary_location,type,cited_by_count,open_access"
            ),
        }
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            return [
                candidate
                for item in response.json().get("results", [])
                if (candidate := self._map(item))
            ]
        except (httpx.HTTPError, ValueError, TypeError):
            return []
        finally:
            if owns_client:
                await client.aclose()

    def _map(self, item: dict[str, Any]) -> SourceCandidate | None:
        title = str(item.get("title") or "").strip()
        abstract = _restore_abstract(item.get("abstract_inverted_index"))
        if not title or not abstract:
            return None
        content = f"{title}\n\n{abstract}"
        authors = [
            str(authorship.get("author", {}).get("display_name"))
            for authorship in item.get("authorships", [])
            if authorship.get("author", {}).get("display_name")
        ]
        location = item.get("primary_location") or {}
        landing = location.get("landing_page_url") or item.get("doi") or item.get("id")
        open_access = item.get("open_access") or {}
        return SourceCandidate(
            source_type="academic",
            title=title,
            uri=str(landing),
            doi=item.get("doi"),
            authors=authors,
            published_at=str(item.get("publication_year"))
            if item.get("publication_year")
            else None,
            content=content,
            passages=split_passages(abstract),
            quality={
                "provider": "OpenAlex",
                "work_type": item.get("type"),
                "cited_by_count": item.get("cited_by_count", 0),
                "is_open_access": bool(open_access.get("is_oa")),
                "primary_source": True,
            },
            license_status="open_access" if open_access.get("is_oa") else "metadata_and_abstract",
        )


def _restore_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions = [(position, word) for word, values in index.items() for position in values]
    return " ".join(word for _, word in sorted(positions))
