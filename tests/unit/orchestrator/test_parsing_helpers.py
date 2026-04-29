from __future__ import annotations

import pytest

from services.orchestrator.app.parsing import (
    UnsupportedMimeTypeError,
    assess_chunk_quality,
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
    assert parsed.metadata["extractor"] == "html_main_content_v1"


def test_extract_parsed_content_preserves_sphinx_link_text() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <body>
            <div class="sidebar">Navigation</div>
            <main>
              <article>
                <p>
                  SearXNG is a free internet metasearch engine which aggregates
                  results from up to <a href="/user/configured_engines.html">251 search engines</a>.
                </p>
                <p>
                  Instances are listed at <a href="https://searx.space/">searx.space</a>;
                  see <a href="/admin/installation.html">installation</a>.
                </p>
              </article>
            </main>
          </body>
        </html>
        """,
    )

    assert "251 search engines" in parsed.text
    assert "searx.space" in parsed.text
    assert "installation" in parsed.text
    assert parsed.metadata["preserved_link_text_count"] >= 3
    assert parsed.metadata["text_cleanup_applied"] is False


def test_extract_parsed_content_cleans_broken_link_residue_conservatively() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html><body><main><p>
          SearXNG is a free internet metasearch engine which aggregates results from up to 251 .
        </p></main></body></html>
        """,
    )

    assert "from up to 251 ." not in parsed.text
    assert "aggregates results." in parsed.text
    assert parsed.metadata["text_cleanup_applied"] is True
    assert "from up to <number> ." in parsed.metadata["dropped_broken_link_fragments"]


def test_extract_parsed_content_prefers_main_article_and_discards_boilerplate() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <body>
            <nav>Jump to content Main menu move to sidebar</nav>
            <main>
              <article>
                <p>SearXNG is a free internet metasearch engine.</p>
                <p>It aggregates results from many search services.</p>
              </article>
            </main>
            <footer>Privacy policy About Wikipedia Edit links</footer>
          </body>
        </html>
        """,
    )

    assert "SearXNG is a free internet metasearch engine." in parsed.text
    assert "It aggregates results from many search services." in parsed.text
    assert "Jump to content" not in parsed.text
    assert "Privacy policy" not in parsed.text


def test_extract_parsed_content_removes_wikipedia_navigation_and_references() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <body>
            <div id="mw-content-text">
              <div class="mw-parser-output">
                <div id="toc">Contents hide</div>
                <p>SearXNG is a free metasearch engine inspired by searx.</p>
                <p>It can aggregate results from search engines without tracking users.</p>
                <div class="reflist">References Implementacion De Un Prototipo (PDF)</div>
                <div class="navbox">Privacy policy About Wikipedia Edit links</div>
              </div>
            </div>
          </body>
        </html>
        """,
    )

    assert "SearXNG is a free metasearch engine" in parsed.text
    assert "aggregate results" in parsed.text
    assert "Contents hide" not in parsed.text
    assert "Privacy policy" not in parsed.text
    assert "Implementacion De Un Prototipo" not in parsed.text
    assert parsed.metadata["removed_boilerplate_count"] >= 3
    assert parsed.metadata["extracted_text_length"] == len(parsed.text)


def test_extract_parsed_content_keeps_wikipedia_parser_output_paragraphs() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <body>
            <div id="content">
              <div id="bodyContent">
                <div id="mw-content-text">
                  <div class="mw-parser-output">
                    <div class="sidebar">Main menu move to sidebar</div>
                    <p>SearXNG is a free and open-source metasearch engine.</p>
                    <p>SearXNG supports over 70 different search engines.</p>
                    <div class="mw-references">Retrieved from reference noise.</div>
                  </div>
                </div>
              </div>
            </div>
          </body>
        </html>
        """,
    )

    assert "SearXNG is a free and open-source metasearch engine." in parsed.text
    assert "SearXNG supports over 70 different search engines." in parsed.text
    assert "Main menu" not in parsed.text
    assert "Retrieved from" not in parsed.text


def test_extract_parsed_content_uses_mediawiki_paragraph_fallback_when_main_empty() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <head><meta name="generator" content="MediaWiki"></head>
          <body>
            <nav>Jump to content Main menu move to sidebar</nav>
            <div>
              <p>SearXNG is a free and open-source metasearch engine.</p>
              <p>SearXNG removes private data from requests sent to search services.</p>
            </div>
            <footer>Privacy policy About Wikipedia Edit links</footer>
          </body>
        </html>
        """,
    )

    assert parsed.metadata["fallback_used"] is True
    assert parsed.metadata["extractor_strategy_used"] == "body_readable_paragraphs"
    assert "SearXNG is a free and open-source metasearch engine." in parsed.text
    assert "SearXNG removes private data" in parsed.text
    assert "Jump to content" not in parsed.text


def test_extract_parsed_content_searxng_wikipedia_like_fixture_is_explanatory() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"""
        <html>
          <body>
            <div id="mw-content-text">
              <div class="mw-parser-output">
                <table class="infobox"><tr><td>Navigation detail</td></tr></table>
                <p><b>SearXNG</b> is a free and open-source metasearch engine.</p>
                <p>SearXNG supports over 70 different search engines.</p>
                <p>SearXNG can separate results into multiple categories.</p>
                <p>SearXNG removes private data from requests sent to search services.</p>
                <div class="navbox">Privacy policy About Wikipedia Edit links</div>
              </div>
            </div>
          </body>
        </html>
        """,
    )

    assert "SearXNG is a free and open-source metasearch engine." in parsed.text
    assert "SearXNG supports over 70 different search engines." in parsed.text
    assert "SearXNG can separate results into multiple categories." in parsed.text
    assert "SearXNG removes private data from requests sent to search services." in parsed.text
    assert "Privacy policy" not in parsed.text


def test_extract_parsed_content_marks_redirect_stub_and_followup_url() -> None:
    parsed = extract_parsed_content(
        mime_type="text/html",
        content=b"<html><body>Redirecting to https://docs.searxng.org/</body></html>",
    )

    assert parsed.text == "Redirecting to https://docs.searxng.org/"
    assert parsed.metadata["content_quality"] == "low"
    assert parsed.metadata["reason"] == "redirect_stub"
    assert parsed.metadata["should_generate_claims"] is False
    assert parsed.metadata["discovered_followup_url"] == "https://docs.searxng.org/"


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


def test_chunk_quality_marks_redirect_navigation_and_reference_chunks_ineligible() -> None:
    redirect_quality = assess_chunk_quality(
        text="Redirecting to https://docs.searxng.org/",
        query="What is SearXNG and how does it work?",
        source_quality_score=0.1,
        parsed_metadata={"reason": "redirect_stub"},
    )
    nav_quality = assess_chunk_quality(
        text="Jump to content Main menu move to sidebar Privacy policy About Wikipedia Edit links",
        query="What is SearXNG and how does it work?",
        source_quality_score=0.7,
        parsed_metadata={},
    )
    reference_quality = assess_chunk_quality(
        text="Implementacion De Un Prototipo (PDF) Bachelor Thesis.",
        query="What is SearXNG and how does it work?",
        source_quality_score=0.7,
        parsed_metadata={},
    )

    assert redirect_quality.eligible_for_claims is False
    assert nav_quality.eligible_for_claims is False
    assert nav_quality.is_navigation_noise is True
    assert reference_quality.eligible_for_claims is False
    assert reference_quality.is_reference_section is True


def test_chunk_quality_marks_explanatory_paragraph_eligible() -> None:
    quality = assess_chunk_quality(
        text=(
            "SearXNG is a free internet metasearch engine that sends search requests "
            "to multiple services and aggregates the results without profiling users."
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.95,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is True
    assert quality.content_quality_score >= 0.35


def test_chunk_quality_keeps_references_heading_chunk_ineligible() -> None:
    quality = assess_chunk_quality(
        text=(
            "References\n\n"
            "Dávila, Anthony Bryan Encalada (2023). Implementación De Un Prototipo "
            "(PDF) (Bachelor Thesis). Retrieved from archive."
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.78,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is False
    assert quality.is_reference_section is True
    assert "reference_section" in quality.reasons


def test_chunk_quality_keeps_privacy_body_eligible_before_reference_tail() -> None:
    quality = assess_chunk_quality(
        text=(
            "Privacy\n\n"
            "SearXNG removes private data from requests sent to search services. "
            "SearXNG itself stores little to no information that can be used to identify users.\n\n"
            "See also\n\n"
            "Free and open-source software portal\n\n"
            "References"
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.78,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is True
    assert quality.is_reference_section is False
    assert quality.content_quality_score >= 0.35


def test_chunk_quality_marks_architecture_diagram_and_config_ineligible() -> None:
    quality = assess_chunk_quality(
        text=(
            "2 Reference architecture of a public SearXNG setup.\n\n"
            "digraph G {\n"
            "  rp -> uwsgi\n"
            "  uwsgi -> valkey\n"
            "}\n\n"
            "use_default_settings:\n"
            "secret_key: change-me\n"
            "valkey://localhost:6379/0"
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.95,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is False
    assert quality.is_diagram_or_config_section is True
    assert "diagram_or_config_section" in quality.reasons


def test_chunk_quality_marks_developer_heading_only_page_ineligible() -> None:
    quality = assess_chunk_quality(
        text="Developer documentation",
        query="What is SearXNG and how does it work?",
        source_quality_score=0.95,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is False
    assert quality.content_quality == "low"
    assert "very_short" in quality.reasons


def test_chunk_quality_marks_documentation_pointer_and_project_meta_ineligible() -> None:
    quality = assess_chunk_quality(
        text=(
            "For more information, visit the documentation. "
            "Track development, send contributions, and join Matrix for project updates."
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.95,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is False
    assert quality.is_navigation_noise is True
    assert "pointer_or_project_meta_noise" in quality.reasons


def test_chunk_quality_keeps_answer_prose_with_documentation_tail_eligible() -> None:
    quality = assess_chunk_quality(
        text=(
            "SearXNG aggregates results from multiple search services and returns them "
            "to the user. For more information, visit the documentation."
        ),
        query="What is SearXNG and how does it work?",
        source_quality_score=0.95,
        parsed_metadata={},
    )

    assert quality.eligible_for_claims is True
    assert quality.is_navigation_noise is False
