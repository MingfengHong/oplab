from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from oplab.retrieval.base import SourceCandidate
from oplab.retrieval.text import split_passages

ALLOWED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}


def candidate_from_bytes(filename: str, data: bytes, uri: str) -> SourceCandidate:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {suffix or 'unknown'}")
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(data))
        pages = [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
        text = "\n\n".join(value for _, value in pages)
        passages = []
        for page_number, page_text in pages:
            for part, passage in enumerate(split_passages(page_text), start=1):
                passages.append(
                    type(passage)(
                        locator=f"page:{page_number}:passage:{part}",
                        text=passage.text,
                        start_char=passage.start_char,
                        end_char=passage.end_char,
                    )
                )
    else:
        text = data.decode("utf-8-sig")
        passages = split_passages(text)
    return SourceCandidate(
        source_type="local_document",
        title=Path(filename).stem,
        uri=uri,
        content=text,
        passages=passages,
        quality={"provider": "user_upload", "primary_source": True},
        license_status="user_supplied",
    )


async def candidate_from_url(url: str, *, max_bytes: int) -> SourceCandidate:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are allowed")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "Oplab/0.1 research ingestion"})
        response.raise_for_status()
        data = response.content
        if len(data) > max_bytes:
            raise ValueError("Remote source exceeds the configured size limit")
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            return candidate_from_bytes(
                Path(parsed.path).name or "source.pdf", data, str(response.url)
            )
        soup = BeautifulSoup(data, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "aside"]):
            element.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else parsed.netloc
        text = "\n\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )
        return SourceCandidate(
            source_type="web",
            title=title,
            uri=str(response.url),
            content=text,
            passages=split_passages(text),
            quality={"provider": "direct_url", "primary_source": False},
            license_status="linked_only",
        )


def content_hash(candidate: SourceCandidate) -> str:
    return hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()


def passage_payload(candidate: SourceCandidate) -> list[dict]:
    return [
        {
            "locator": passage.locator,
            "text": passage.text,
            "start_char": passage.start_char,
            "end_char": passage.end_char,
            "passage_hash": hashlib.sha256(passage.text.encode("utf-8")).hexdigest(),
        }
        for passage in candidate.passages
    ]
