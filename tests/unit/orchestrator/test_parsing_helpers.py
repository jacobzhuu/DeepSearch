from __future__ import annotations

import pytest

from services.orchestrator.app.parsing import (
    UnsupportedMimeTypeError,
    chunk_text,
    extract_parsed_content,
)


def test_extract_parsed_content_from_html_discards_script_and_keeps_title() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html; charset=utf-8",
        content=(
            b"<html><head><title>Example Title</title><script>bad()</script></head>"
            b"<body><h1>Heading</h1><p>First paragraph.</p><p>Second paragraph.</p></body></html>"
        ),
    )

    assert parsed.source_type == "web_page"
    assert parsed.title == "Example Title"
    assert "bad()" not in parsed.text
    assert "Heading" in parsed.text
    assert "First paragraph." in parsed.text
    assert parsed.metadata["extractor"] == "html_text_v1"


def test_extract_parsed_content_from_short_html_uses_title_as_text_fallback() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"<html><head><title>SearXNG</title></head><body></body></html>",
    )

    assert parsed.title == "SearXNG"
    assert parsed.text == "SearXNG"


def test_extract_parsed_content_from_plain_text_derives_title() -> None:
    parsed = extract_parsed_content(
        mime_type="text/plain",
        content=b"Line one title\n\nLine two body\nLine three body",
    )

    assert parsed.source_type == "plain_text"
    assert parsed.title == "Line one title"
    assert parsed.text == "Line one title\n\nLine two body\nLine three body"
    assert parsed.metadata["extractor"] == "plain_text_v1"


def test_extract_parsed_content_rejects_unsupported_mime_type() -> None:
    with pytest.raises(UnsupportedMimeTypeError):
        extract_parsed_content(
            mime_type="application/pdf",
            content=b"%PDF-1.7",
        )


def test_chunk_text_uses_stable_paragraph_windows() -> None:
    chunks = chunk_text(
        "Paragraph one.\n\nParagraph two is a little longer.\n\nParagraph three.",
        max_chars_per_chunk=40,
    )

    assert [chunk.chunk_no for chunk in chunks] == [0, 1, 2]
    assert chunks[0].metadata["strategy"] == "paragraph_window_v1"
    assert chunks[0].metadata["paragraph_count"] == 1
    assert chunks[1].metadata["paragraph_count"] == 1
    assert chunks[0].token_count >= 1


def test_chunk_text_splits_single_long_paragraph() -> None:
    chunks = chunk_text("x" * 90, max_chars_per_chunk=40)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= 40 for chunk in chunks)
