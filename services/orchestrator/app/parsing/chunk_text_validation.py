"""Pre-insert validation for ``SourceChunk.text``.

Covers DB ``ck_source_chunk_text_non_empty`` and basic binary/PDF-tail heuristics.
"""

from __future__ import annotations

import hashlib
from typing import Any

from services.orchestrator.app.parsing.chunking import ParsedChunk

REJECT_EMPTY_CHUNK_TEXT = "empty_chunk_text"
REJECT_WHITESPACE_ONLY_CHUNK_TEXT = "whitespace_only_chunk_text"
REJECT_BINARY_LIKE_CHUNK_TEXT = "binary_like_chunk_text"
REJECT_PDF_STREAM_RESIDUE = "pdf_stream_residue"
REJECT_LOW_PRINTABLE_RATIO = "low_printable_ratio"
REJECT_INVALID_CHUNK_TEXT = "invalid_chunk_text"

# Tunables: binary PDF tails and damaged extractors skew low; normal prose is high.
_MIN_PRINTABLE_RATIO = 0.42
_MAX_CONTROL_RATIO = 0.14
_PDFISH_WINDOW = 6000

_PDFISH_TOKENS = (
    "endobj",
    "endstream",
    "xref",
    "startxref",
    "trailer",
    "/length",
    "/filter",
    "flatedecode",
    "/type/catalog",
    "/type/pages",
    "obj",
    "<<",
    ">>",
)


def text_fingerprint(text: str, *, max_bytes: int = 256) -> str:
    """Short stable fingerprint for logs (never log raw binary-heavy text)."""
    raw = text.encode("utf-8", errors="replace")[:max_bytes]
    return hashlib.sha256(raw).hexdigest()[:16]


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    ok = 0
    for ch in text:
        if ch.isprintable() or ch in "\n\r\t":
            ok += 1
    return ok / len(text)


def _control_ratio(text: str) -> float:
    if not text:
        return 0.0
    ctrl = 0
    for ch in text:
        o = ord(ch)
        if o < 32 and ch not in "\n\r\t\f\v":
            ctrl += 1
    return ctrl / len(text)


def _looks_like_pdf_stream_residue(text: str) -> bool:
    head = text[:_PDFISH_WINDOW].lower()
    hits = sum(1 for tok in _PDFISH_TOKENS if tok in head)
    pr = _printable_ratio(text)
    # Raw PDF object streams: many operators, low human-readable ratio.
    if hits >= 5 and pr < 0.55:
        return True
    if "stream" in head and "endstream" in head and pr < 0.5:
        return True
    return False


def validate_chunk_text_for_insert(text: object | None) -> tuple[bool, str | None]:
    """
    Return ``(True, None)`` if the chunk text is safe to persist.

    Otherwise ``(False, rejection_reason)`` with a bounded reason code.
    """
    if text is None:
        return False, REJECT_EMPTY_CHUNK_TEXT
    if not isinstance(text, str):
        return False, REJECT_INVALID_CHUNK_TEXT
    if "\x00" in text:
        return False, REJECT_BINARY_LIKE_CHUNK_TEXT
    stripped = text.strip()
    if not stripped:
        return (
            False,
            REJECT_WHITESPACE_ONLY_CHUNK_TEXT if len(text) > 0 else REJECT_EMPTY_CHUNK_TEXT,
        )
    work = stripped
    pr = _printable_ratio(work)
    cr = _control_ratio(work)
    if _looks_like_pdf_stream_residue(work):
        return False, REJECT_PDF_STREAM_RESIDUE
    if pr < _MIN_PRINTABLE_RATIO:
        return False, REJECT_LOW_PRINTABLE_RATIO
    if cr > _MAX_CONTROL_RATIO:
        return False, REJECT_BINARY_LIKE_CHUNK_TEXT
    # High ratio of replacement / private-use characters often indicates mojibake / binary.
    weird = sum(1 for ch in work if ord(ch) >= 0xFFF0 or ("\ufffd" == ch))
    if weird / len(work) > 0.08:
        return False, REJECT_BINARY_LIKE_CHUNK_TEXT
    return True, None


def partition_valid_parsed_chunks(
    parsed_chunks: list[ParsedChunk],
    *,
    content_snapshot_id: Any,
    canonical_url: str | None,
    domain: str | None,
) -> tuple[list[ParsedChunk], dict[str, Any]]:
    """
    Filter ``ParsedChunk`` rows, renumbering ``chunk_no`` for valid inserts.

    Returns ``(valid_chunks, diagnostics)`` where diagnostics is JSON-safe.
    """

    rejections: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    valid: list[ParsedChunk] = []
    for pc in parsed_chunks:
        ok, reason = validate_chunk_text_for_insert(pc.text)
        if ok:
            valid.append(pc)
            continue
        r = reason or REJECT_INVALID_CHUNK_TEXT
        reason_counts[r] = reason_counts.get(r, 0) + 1
        rejections.append(
            {
                "chunk_no": int(pc.chunk_no),
                "reason": r,
                "text_fingerprint": text_fingerprint(pc.text),
                "char_len": len(pc.text),
            }
        )

    renumbered: list[ParsedChunk] = []
    for i, pc in enumerate(valid):
        renumbered.append(
            ParsedChunk(
                chunk_no=i,
                text=pc.text,
                token_count=pc.token_count,
                metadata=dict(pc.metadata),
            )
        )

    diagnostics: dict[str, Any] = {
        "invalid_chunk_rejection_count": len(rejections),
        "invalid_chunk_rejection_reason_distribution": dict(sorted(reason_counts.items())),
        "rejected_chunk_samples": rejections[:12],
        "content_snapshot_id": str(content_snapshot_id),
        "canonical_url": canonical_url,
        "domain": domain,
    }
    return renumbered, diagnostics
