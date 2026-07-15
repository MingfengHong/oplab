from __future__ import annotations

import re

from oplab.retrieval.base import PassageCandidate


def split_passages(text: str, *, max_chars: int = 1_500) -> list[PassageCandidate]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    passages: list[PassageCandidate] = []
    cursor = 0
    part = 1
    for block in blocks:
        for start in range(0, len(block), max_chars):
            value = block[start : start + max_chars].strip()
            if not value:
                continue
            absolute_start = normalized.find(value, cursor)
            absolute_start = cursor if absolute_start < 0 else absolute_start
            absolute_end = absolute_start + len(value)
            passages.append(
                PassageCandidate(
                    locator=f"passage:{part}",
                    text=value,
                    start_char=absolute_start,
                    end_char=absolute_end,
                )
            )
            cursor = absolute_end
            part += 1
    return passages


def first_substantive_sentence(text: str, max_chars: int = 360) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    sentence = re.split(r"(?<=[.!?。！？])\s+", clean, maxsplit=1)[0]
    return sentence[:max_chars].rstrip()
