from __future__ import annotations

from services.orchestrator.app.parsing.chunk_text_validation import (
    REJECT_BINARY_LIKE_CHUNK_TEXT,
    REJECT_EMPTY_CHUNK_TEXT,
    REJECT_INVALID_CHUNK_TEXT,
    REJECT_LOW_PRINTABLE_RATIO,
    REJECT_PDF_STREAM_RESIDUE,
    REJECT_WHITESPACE_ONLY_CHUNK_TEXT,
    partition_valid_parsed_chunks,
    validate_chunk_text_for_insert,
)
from services.orchestrator.app.parsing.chunking import ParsedChunk


def test_validate_rejects_none() -> None:
    ok, reason = validate_chunk_text_for_insert(None)
    assert ok is False
    assert reason == REJECT_EMPTY_CHUNK_TEXT


def test_validate_rejects_non_string() -> None:
    ok, reason = validate_chunk_text_for_insert(b"not-a-str")  # type: ignore[arg-type]
    assert ok is False
    assert reason == REJECT_INVALID_CHUNK_TEXT


def test_validate_rejects_empty_and_whitespace() -> None:
    assert validate_chunk_text_for_insert("") == (False, REJECT_EMPTY_CHUNK_TEXT)
    assert validate_chunk_text_for_insert("   \n\t  ") == (False, REJECT_WHITESPACE_ONLY_CHUNK_TEXT)


def test_validate_accepts_plain_text() -> None:
    assert validate_chunk_text_for_insert("Hello world.\n\nSecond paragraph.") == (True, None)


def test_validate_rejects_nul_binary() -> None:
    ok, reason = validate_chunk_text_for_insert("hello\x00world")
    assert ok is False
    assert reason == REJECT_BINARY_LIKE_CHUNK_TEXT


def test_validate_rejects_low_printable_ratio() -> None:
    junk = "\x01\x02\x03" * 40 + "hi"
    ok, reason = validate_chunk_text_for_insert(junk)
    assert ok is False
    assert reason == REJECT_LOW_PRINTABLE_RATIO


def test_validate_rejects_pdf_stream_residue() -> None:
    # ``_looks_like_pdf_stream_residue`` requires multiple PDF-ish tokens and a low
    # printable ratio in the full string (ASCII operators alone are too readable).
    head = (
        "1 0 obj\n<< /Type /Catalog >>\nendobj\n" * 3
        + "stream\n"
        + ("\x01" * 500 + "endstream\nendobj\n") * 2
        + "xref\n0 5\ntrailer\nstartxref\n"
        + ("/Filter /FlateDecode\n<< /Length 1 >>\nobj\n>>\n") * 5
    )
    s = head + ("\x01\x02" * 2000)
    ok, reason = validate_chunk_text_for_insert(s)
    assert ok is False
    assert reason == REJECT_PDF_STREAM_RESIDUE


def test_partition_renumbers_and_skips_invalid() -> None:
    chunks = [
        ParsedChunk(0, "Valid chunk one.", 4, {"char_start": 0}),
        ParsedChunk(1, "\x00bad", 2, {}),
        ParsedChunk(2, "Valid chunk two.", 4, {}),
    ]
    valid, diag = partition_valid_parsed_chunks(
        chunks,
        content_snapshot_id="snap-1",
        canonical_url="https://ex.com/a",
        domain="ex.com",
    )
    assert len(valid) == 2
    assert valid[0].chunk_no == 0 and valid[0].text == "Valid chunk one."
    assert valid[1].chunk_no == 1 and valid[1].text == "Valid chunk two."
    assert diag["invalid_chunk_rejection_count"] == 1
    assert REJECT_BINARY_LIKE_CHUNK_TEXT in diag["invalid_chunk_rejection_reason_distribution"]


def test_partition_empty_input() -> None:
    valid, diag = partition_valid_parsed_chunks(
        [],
        content_snapshot_id="snap-1",
        canonical_url=None,
        domain=None,
    )
    assert valid == []
    assert diag["invalid_chunk_rejection_count"] == 0
