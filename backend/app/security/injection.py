"""Direct and indirect prompt-injection detection.

Heuristic-first: cheap, deterministic and testable. A model-based classifier can be
layered on later, but this layer must never be removed.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass

# Instructions aimed at an assistant. Indicative, not exhaustive — the corpus only grows.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(r"\bignore\s+(all\s+)?(the\s+)?(previous|prior|above)\b", re.I),
    ),
    (
        "instruction_override",
        re.compile(r"\bdisregard\s+(all\s+)?(previous|prior|your)\b", re.I),
    ),
    (
        "instruction_override",
        re.compile(r"\bforget\s+(everything|all|your\s+instructions)\b", re.I),
    ),
    (
        "role_hijack",
        re.compile(r"\byou\s+are\s+now\b|\bact\s+as\s+(if|a)\b|\bpretend\s+to\s+be\b", re.I),
    ),
    ("role_hijack", re.compile(r"^\s*(system|assistant)\s*:", re.I | re.M)),
    (
        "prompt_extraction",
        re.compile(
            r"\b(reveal|show|print|repeat|output)\b.{0,30}\b(system\s+prompt|instructions|rules)\b",
            re.I,
        ),
    ),
    (
        "prompt_extraction",
        re.compile(r"\bwhat\s+(are|were)\s+your\s+(original\s+)?instructions\b", re.I),
    ),
    ("delimiter_escape", re.compile(r"</?untrusted_document_content", re.I)),
    ("delimiter_escape", re.compile(r"<\|(im_start|im_end|endoftext)\|>", re.I)),
    (
        "tool_injection",
        re.compile(r"\bcall\s+the\s+\w+\s+tool\b|\btool_call\b|\bfunction_call\b", re.I),
    ),
    (
        "scope_escalation",
        re.compile(r"\b(tenant_id|department|owner_id)\s*[=:]\s*['\"]?\w+", re.I),
    ),
    ("scope_escalation", re.compile(r"\b(all|other|another)\s+tenants?\b", re.I)),
    (
        "embedded_directive",
        re.compile(r"\b(AI|assistant|model|chatbot)[,:]?\s+(when|if)\s+(asked|queried)\b", re.I),
    ),
    (
        "embedded_directive",
        re.compile(r"\bimportant\s+instructions?\s+for\s+(the\s+)?(AI|assistant|model)\b", re.I),
    ),
    (
        "exfiltration",
        re.compile(r"\b(send|post|upload|exfiltrate)\b.{0,25}\b(to\s+https?://|webhook)", re.I),
    ),
]

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_B64_BLOB = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCRIPT = re.compile(r"<\s*(script|iframe|object|embed|svg)\b", re.I)


@dataclass(frozen=True)
class ScanResult:
    blocked: bool
    categories: tuple[str, ...]
    detail: str = ""

    @property
    def clean(self) -> bool:
        return not self.blocked


def normalize(text: str) -> str:
    """Strip evasion tricks before matching. Applied to input and to document content."""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_ZERO_WIDTH)


def _try_decode(blob: str) -> str | None:
    """Decode one candidate blob. A blob that will not decode is simply not a payload."""
    pad = "=" * (-len(blob) % 4)
    try:
        decoded = base64.b64decode(blob + pad, validate=False).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 - malformed base64 is not an injection attempt
        return None
    return decoded if decoded.isprintable() and len(decoded) > 12 else None


def _decoded_variants(text: str) -> list[str]:
    """Surface base64-hidden payloads so the patterns can see them."""
    candidates = (_try_decode(blob) for blob in _B64_BLOB.findall(text)[:5])
    return [c for c in candidates if c]


def scan(text: str, *, source: str = "user") -> ScanResult:
    """Scan text for injection. `source` is 'user' (direct) or 'document' (indirect)."""
    if not text:
        return ScanResult(False, ())

    candidates = [normalize(text)]
    candidates.extend(_decoded_variants(candidates[0]))

    hits: set[str] = set()
    for candidate in candidates:
        for name, pattern in _PATTERNS:
            if pattern.search(candidate):
                hits.add(name)
        if _SCRIPT.search(candidate):
            hits.add("active_content")
        if source == "document" and _HTML_COMMENT.search(candidate):
            for comment in _HTML_COMMENT.findall(candidate):
                for _name, pattern in _PATTERNS:
                    if pattern.search(comment):
                        hits.add("hidden_directive")

    return ScanResult(bool(hits), tuple(sorted(hits)), detail=f"source={source}")


def scan_document_element(text: str) -> ScanResult:
    return scan(text, source="document")
