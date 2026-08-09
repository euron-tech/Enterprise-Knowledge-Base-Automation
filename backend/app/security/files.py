"""Upload validation: magic-byte sniffing, extension allow-list, filename sanitation."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from app.core.constants import ALLOWED_MIME_TYPES
from app.core.errors import UnsupportedMediaTypeError, ValidationError

_MAGIC: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"RIFF", "audio/wav"),
]

_OOXML = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_EXT = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".csv": "text/csv",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE = re.compile(r"[^\w.\- ]", re.UNICODE)
MAX_FILENAME_LEN = 200


def sanitize_filename(name: str) -> str:
    """Reject traversal; strip control chars and homoglyphs; bound the length."""
    if not name or not name.strip():
        raise ValidationError("empty filename")

    name = unicodedata.normalize("NFKC", name)
    name = _CONTROL.sub("", name)

    # Take the basename only — kills ../.. and absolute paths, POSIX or Windows.
    name = name.replace("\\", "/")
    base = PurePosixPath(name).name
    if not base or base in {".", ".."}:
        raise ValidationError("invalid filename")

    stem = PurePosixPath(base).stem
    suffix = PurePosixPath(base).suffix.lower()
    stem = _UNSAFE.sub("_", stem).strip(" .") or "file"
    stem = stem[: MAX_FILENAME_LEN - len(suffix)]
    return f"{stem}{suffix}"


def sniff_mime(data: bytes, filename: str) -> str:
    """Determine content type from bytes, never from the client's declared type."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            if mime == "audio/wav" and data[8:12] != b"WAVE":
                continue
            return mime

    suffix = PurePosixPath(filename).suffix.lower()

    # OOXML files are zips; distinguish by extension after confirming the zip magic.
    if data.startswith(b"PK\x03\x04") and suffix in _OOXML:
        return _OOXML[suffix]

    if data[4:8] == b"ftyp":
        return "video/mp4"

    if suffix in _TEXT_EXT:
        try:
            data[:2048].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMediaTypeError(f"{suffix} is not valid utf-8") from exc
        return _TEXT_EXT[suffix]

    raise UnsupportedMediaTypeError(f"unrecognised content for {filename!r}")


def validate_upload(data: bytes, filename: str, max_bytes: int) -> tuple[str, str]:
    """Return (safe_filename, mime). Raises before any parsing happens."""
    if not data:
        raise ValidationError("empty file")
    if len(data) > max_bytes:
        from app.core.errors import PayloadTooLargeError

        raise PayloadTooLargeError(f"{len(data)} bytes exceeds {max_bytes}")

    safe = sanitize_filename(filename)
    mime = sniff_mime(data, safe)
    if mime not in ALLOWED_MIME_TYPES:
        raise UnsupportedMediaTypeError(f"{mime} is not an allowed type")
    return safe, mime
