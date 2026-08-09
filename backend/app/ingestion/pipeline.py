"""Ingestion: parse -> bridge modalities to text -> scan -> chunk -> embed -> upsert.

The gateway's embedding endpoint accepts text only, so images are described by chat
vision and audio is transcribed before embedding. Raw assets stay in object storage
and remain what a citation points at.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.clients.euri import EuriClient
from app.clients.vectorstore import VectorStore
from app.core.config import Settings
from app.core.logging import get_logger, log_event
from app.security.injection import scan_document_element

logger = get_logger(__name__)

APPROX_CHARS_PER_TOKEN = 4


@dataclass
class Element:
    text: str
    page_number: int = 1
    modality: str = "text"
    element_type: str = "paragraph"
    time_offset_ms: int | None = None
    quarantined: bool = False
    quarantine_reason: tuple[str, ...] = ()


@dataclass
class IngestResult:
    document_id: str
    chunks_written: int
    quarantined: int
    elements: int
    checksum: str
    pages: int
    warnings: list[str] = field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ parsing
def parse_text(data: bytes) -> list[Element]:
    text = data.decode("utf-8", errors="replace")
    return _split_by_headings(text)


def _split_by_headings(text: str) -> list[Element]:
    """Heading-aware split so a chunk never straddles two sections."""
    blocks = re.split(r"\n(?=#{1,6}\s)", text)
    out: list[Element] = []
    for i, block in enumerate(blocks, start=1):
        block = block.strip()
        if block:
            out.append(Element(text=block, page_number=i, element_type="section"))
    return out or [Element(text=text.strip() or "", page_number=1)]


def parse_csv(data: bytes) -> list[Element]:
    """Serialize tables to Markdown; never split a row."""
    import csv

    text = data.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return [Element(text="\n".join(lines), element_type="table", modality="table")]


def parse_pdf(data: bytes) -> list[Element]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return [Element(text="", element_type="unparsed")]
    reader = PdfReader(io.BytesIO(data))
    out: list[Element] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            out.append(Element(text=text, page_number=i, element_type="page"))
    return out


async def parse_image(data: bytes, mime: str, euri: EuriClient) -> list[Element]:
    """The text bridge: vision describes the image, the description gets embedded."""
    b64 = base64.b64encode(data).decode()
    description = await euri.describe_image(b64, mime=mime)
    return [Element(text=description, modality="image", element_type="image_description")]


async def parse_audio(data: bytes, filename: str, euri: EuriClient) -> list[Element]:
    transcript = await euri.transcribe(data, filename=filename)
    if not transcript.strip():
        return []
    return [Element(text=transcript, modality="audio", element_type="transcript", time_offset_ms=0)]


async def parse(data: bytes, mime: str, filename: str, euri: EuriClient) -> list[Element]:
    if mime == "application/pdf":
        return parse_pdf(data)
    if mime == "text/csv":
        return parse_csv(data)
    if mime.startswith("text/"):
        return parse_text(data)
    if mime.startswith("image/"):
        return await parse_image(data, mime, euri)
    if mime.startswith("audio/") or mime.startswith("video/"):
        return await parse_audio(data, filename, euri)
    return parse_text(data)


# ------------------------------------------------------------------ chunking
def chunk_elements(elements: list[Element], max_tokens: int) -> list[Element]:
    """Structure-aware. Tables stay whole. Our own ceiling, never the gateway's."""
    limit = max_tokens * APPROX_CHARS_PER_TOKEN
    overlap = min(400, limit // 6)
    out: list[Element] = []

    for el in elements:
        if el.element_type == "table" or len(el.text) <= limit:
            if el.text.strip():
                out.append(el)
            continue
        text = el.text
        start = 0
        while start < len(text):
            end = min(start + limit, len(text))
            if end < len(text):
                cut = text.rfind("\n", start + limit // 2, end)
                if cut == -1:
                    cut = text.rfind(" ", start + limit // 2, end)
                if cut > start:
                    end = cut
            piece = text[start:end].strip()
            if piece:
                out.append(
                    Element(
                        text=piece,
                        page_number=el.page_number,
                        modality=el.modality,
                        element_type=el.element_type,
                        time_offset_ms=el.time_offset_ms,
                    )
                )
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return out


def deterministic_chunk_id(document_id: str, version: int, ordinal: int) -> str:
    return hashlib.sha256(f"{document_id}|{version}|{ordinal}".encode()).hexdigest()[:32]


# ------------------------------------------------------------------ pipeline
class IngestionPipeline:
    def __init__(self, settings: Settings, euri: EuriClient, vectors: VectorStore) -> None:
        self.settings = settings
        self.euri = euri
        self.vectors = vectors

    async def ingest(
        self,
        *,
        data: bytes,
        filename: str,
        mime: str,
        tenant_id: str,
        department: str,
        owner_id: str,
        document_id: str | None = None,
        version: int = 1,
        source_uri: str = "",
    ) -> IngestResult:
        document_id = document_id or str(uuid.uuid4())
        checksum = sha256_bytes(data)
        warnings: list[str] = []

        elements = await parse(data, mime, filename, self.euri)

        # Indirect-injection scan on EVERY element, before anything is indexed.
        kept: list[Element] = []
        quarantined = 0
        for el in elements:
            result = scan_document_element(el.text)
            if result.blocked:
                quarantined += 1
                el.quarantined = True
                el.quarantine_reason = result.categories
                log_event(
                    logger,
                    logging.WARNING,
                    "ingest.element_quarantined",
                    document_id=document_id,
                    categories=list(result.categories),
                    page=el.page_number,
                )
                continue
            kept.append(el)

        chunks = chunk_elements(kept, self.settings.max_chunk_tokens)

        # Our own ceiling — the gateway returns 200 and silently truncates otherwise.
        hard_limit = self.settings.max_chunk_tokens * APPROX_CHARS_PER_TOKEN
        for c in chunks:
            if len(c.text) > hard_limit:
                c.text = c.text[:hard_limit]
                warnings.append("chunk truncated by local ceiling")

        if not chunks:
            return IngestResult(document_id, 0, quarantined, len(elements), checksum, 0, warnings)

        vectors = await self.euri.embed([c.text for c in chunks])
        created_at = datetime.now(UTC).isoformat()
        pages = max((c.page_number for c in chunks), default=1)

        points = []
        for ordinal, (el, vec) in enumerate(zip(chunks, vectors, strict=True)):
            chunk_id = deterministic_chunk_id(document_id, version, ordinal)
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                    "vector": vec,
                    "payload": {
                        # the ten mandatory fields
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "document_name": filename,
                        "page_number": el.page_number,
                        "source_uri": source_uri or f"s3://{tenant_id}/{document_id}/v{version}",
                        "owner_id": owner_id,
                        "tenant_id": tenant_id,
                        "document_version": version,
                        "checksum": checksum,
                        "created_at": created_at,
                        # operational
                        "department": department,
                        "modality": el.modality,
                        "element_type": el.element_type,
                        "status": "active",
                        "time_offset_ms": el.time_offset_ms,
                        "text": el.text,
                    },
                }
            )

        written = await self.vectors.upsert(points)
        return IngestResult(
            document_id=document_id,
            chunks_written=written,
            quarantined=quarantined,
            elements=len(elements),
            checksum=checksum,
            pages=pages,
            warnings=warnings,
        )
