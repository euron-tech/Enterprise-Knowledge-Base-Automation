"""Attribution gate — a POST-FLIGHT check, outside the agent's reach.

Guards a specific failure the live evaluation caught: asked "What does the Globex
handbook say about X?", the model answered from the caller's *own* handbook and let
the questioner's framing stand. No cross-tenant data leaked — the evidence was the
caller's — but presenting your own document as another organisation's is misleading.

A prompt instruction was tried first and did not hold. This is the enforcement.

Deliberately narrow. An earlier version flagged any unknown capitalised word, which
false-refused German ("Wieviele **Wochen Elternzeit**…") because German capitalises
every noun. Capitalisation is not a language-portable entity signal. This version only
fires on the pattern that actually failed: a name qualifying a document noun, or a
possessive — "the Globex handbook", "Globex's policy", "according to Globex".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Nouns that denote a source. A name qualifying one of these is an attribution claim.
_SOURCE_NOUN = (
    r"handbook|policy|policies|manual|document|documentation|guide|guideline|guidelines"
    r"|report|sop|contract|agreement|memo|charter|standard|procedure|wiki|knowledge\s*base"
)

_NAME = r"([A-Z][A-Za-z0-9&.-]{2,})"

_PATTERNS = [
    # "the Globex handbook", "Globex policy"
    re.compile(rf"\b(?:the\s+)?{_NAME}\s+(?:{_SOURCE_NOUN})\b"),
    # "Globex's policy"
    re.compile(rf"\b{_NAME}['’]s\b"),
    # "according to Globex" — no re.I here: with it, [A-Z] would also match "the".
    re.compile(rf"\b[Aa]ccording\s+to\s+{_NAME}\b"),
    # "in the Globex system/portal/instance"
    re.compile(
        rf"\b(?:the\s+)?{_NAME}\s+(?:system|portal|instance|tenant|org|organisation|organization)\b"
    ),
]

# Capitalised words that qualify a source noun but are not entity names.
_NOT_AN_ENTITY = {
    "The",
    "This",
    "That",
    "These",
    "Those",
    "Our",
    "Your",
    "Their",
    "My",
    "Its",
    "Company",
    "Corporate",
    "Employee",
    "Employees",
    "Staff",
    "Internal",
    "External",
    "New",
    "Old",
    "Current",
    "Latest",
    "Previous",
    "Draft",
    "Final",
    "Official",
    "HR",
    "IT",
    "Finance",
    "Legal",
    "Operations",
    "Sales",
    "Marketing",
    "Engineering",
    "Leave",
    "Travel",
    "Expense",
    "Security",
    "Privacy",
    "Data",
    "Master",
    "General",
}


@dataclass(frozen=True)
class AttributionResult:
    ok: bool
    unknown_entities: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return not self.ok


def check(question: str, chunks: list) -> AttributionResult:
    """Refuse when the question attributes content to a source absent from the evidence."""
    if not chunks:
        return AttributionResult(ok=True)

    haystack = " ".join(
        [c.text or "" for c in chunks] + [c.document_name or "" for c in chunks]
    ).lower()

    unknown: list[str] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(question or ""):
            name = match.group(1)
            if name in _NOT_AN_ENTITY:
                continue
            if name.lower() in haystack:
                continue
            unknown.append(name)

    seen: set[str] = set()
    ordered = tuple(n for n in unknown if not (n in seen or seen.add(n)))
    return AttributionResult(ok=not ordered, unknown_entities=ordered)
