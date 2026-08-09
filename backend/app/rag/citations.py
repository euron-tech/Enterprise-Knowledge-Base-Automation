"""Citation validation — a POST-FLIGHT GATE, outside the agent's reach.

Prompts asking a model to cite honestly are advisory. This is enforcement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.clients.vectorstore import Chunk

_CITATION = re.compile(r"\[([A-Za-z0-9_\-:.]{4,80})\]")


@dataclass
class CitationResult:
    text: str
    citations: list[dict[str, Any]]
    stripped: list[str]
    coverage: float

    @property
    def has_valid_citation(self) -> bool:
        return bool(self.citations)


def validate(answer: str, retrieved: list[Chunk]) -> CitationResult:
    """Every citation must map to a chunk retrieved in THIS request, or it is removed."""
    by_id = {c.chunk_id: c for c in retrieved}
    found = _CITATION.findall(answer or "")

    kept: dict[str, Chunk] = {}
    stripped: list[str] = []
    text = answer or ""

    for raw in found:
        chunk = by_id.get(raw)
        if chunk is None:
            # Not a real retrieved chunk. Remove the marker — never leave a fake citation.
            stripped.append(raw)
            text = text.replace(f"[{raw}]", "")
        else:
            kept[raw] = chunk

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text).strip()

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", answer or "") if s.strip()]
    cited_sentences = sum(1 for s in sentences if _CITATION.search(s))
    coverage = (cited_sentences / len(sentences)) if sentences else 0.0

    return CitationResult(
        text=text,
        citations=[c.citation() for c in kept.values()],
        stripped=stripped,
        coverage=round(coverage, 3),
    )


def score_confidence(
    chunks: list[Chunk], coverage: float, *, stripped: int, guardrail_hits: int
) -> float:
    """Documented and reproducible. Reported honestly; never used to justify a guess."""
    if not chunks:
        return 0.0
    top = max(c.score for c in chunks)
    mean = sum(c.score for c in chunks) / len(chunks)
    retrieval = min(max((0.6 * top + 0.4 * mean), 0.0), 1.0)
    score = 0.6 * retrieval + 0.4 * coverage
    score -= 0.1 * stripped
    score -= 0.2 * guardrail_hits
    return round(min(max(score, 0.0), 1.0), 3)
