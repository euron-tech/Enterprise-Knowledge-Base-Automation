"""Output-side gates. These run AFTER the agent and are outside its reach by design."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECRET_PATTERNS = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("euri_key", re.compile(r"euri-[0-9a-f]{32,}", re.I)),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "conn_string",
        re.compile(r"(?i)(postgres|mysql|redis|mongodb)(\+\w+)?://[^\s]{8,}"),
    ),
    (
        "generic_secret",
        re.compile(r"(?i)\b(api[_-]?key|password|secret)\b\s*[:=]\s*\S{8,}"),
    ),
]

_PII_PATTERNS = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

# Fragments that would indicate the system prompt leaked into the answer.
_PROMPT_FINGERPRINTS = [
    "you are the ekba",
    "answer only from the retrieved context",
    "untrusted_document_content",
    "instruction hierarchy",
    "system rules >",
    "never invent a citation",
]

_ACTIVE_HTML = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|svg|link|meta|style)\b[^>]*>", re.I
)
_EVENT_ATTR = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_URL = re.compile(r"(?i)\b(javascript|data|vbscript)\s*:")

REDACTED = "[REDACTED]"


@dataclass
class GuardOutcome:
    text: str
    blocked: bool = False
    redactions: list[str] = field(default_factory=list)
    reason: str = ""


def sanitize_html(text: str) -> str:
    text = _ACTIVE_HTML.sub("", text)
    text = _EVENT_ATTR.sub("", text)
    return _JS_URL.sub("", text)


def detect_prompt_leak(text: str) -> bool:
    low = text.lower()
    return sum(1 for f in _PROMPT_FINGERPRINTS if f in low) >= 1


def apply(text: str, *, redact_pii: bool = False) -> GuardOutcome:
    """Run every output gate. Redacts or blocks; never warns-and-continues."""
    redactions: list[str] = []

    if detect_prompt_leak(text):
        return GuardOutcome(
            text="",
            blocked=True,
            redactions=["system_prompt"],
            reason="system_prompt_leak",
        )

    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(REDACTED, text)
            redactions.append(name)

    if redact_pii:
        for name, pattern in _PII_PATTERNS:
            if pattern.search(text):
                text = pattern.sub(REDACTED, text)
                redactions.append(name)

    cleaned = sanitize_html(text)
    if cleaned != text:
        redactions.append("active_html")

    return GuardOutcome(text=cleaned, blocked=False, redactions=redactions)
