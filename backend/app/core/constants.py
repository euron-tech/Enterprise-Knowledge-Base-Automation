"""Frozen contracts. Import these; never retype them."""

from typing import Final

# The refusal string is a product contract. Byte-exact. Never translated in storage.
INSUFFICIENT_EVIDENCE: Final[str] = (
    "I could not find enough evidence in the approved documents to answer this question."
)

UNTRUSTED_OPEN: Final[str] = "<untrusted_document_content"
UNTRUSTED_CLOSE: Final[str] = "</untrusted_document_content>"

# Fields the model may never supply — injected server-side from the verified JWT.
PRINCIPAL_FIELDS: Final[frozenset[str]] = frozenset(
    {"tenant_id", "department", "departments", "owner_id", "role", "user_id"}
)

MANDATORY_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "document_id",
    "chunk_id",
    "document_name",
    "page_number",
    "source_uri",
    "owner_id",
    "tenant_id",
    "document_version",
    "checksum",
    "created_at",
)

ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "image/png",
        "image/jpeg",
        "audio/mpeg",
        "audio/wav",
        "video/mp4",
    }
)


class TerminalReason:
    ANSWERED = "answered"
    REFUSED_INSUFFICIENT_EVIDENCE = "refused_insufficient_evidence"
    REFUSED_GUARDRAIL = "refused_guardrail"
    REFUSED_OUT_OF_SCOPE = "refused_out_of_scope"
    CLARIFICATION_REQUESTED = "clarification_requested"
    LIMIT_EXCEEDED = "limit_exceeded"
    UPSTREAM_FAILURE = "upstream_failure"
