from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


class UnsupportedMimeTypeError(Exception):
    def __init__(self, mime_type: str) -> None:
        super().__init__(f"unsupported mime type: {mime_type}")
        self.mime_type = mime_type


@dataclass(frozen=True)
class ParsedContent:
    text: str
    title: str | None
    source_type: str
    metadata: dict[str, object] = field(default_factory=dict)


def extract_parsed_content(*, mime_type: str, content: bytes) -> ParsedContent:
    normalized_mime_type = mime_type.split(";", 1)[0].strip().lower()
    if normalized_mime_type == "text/plain":
        text = _normalize_plain_text(content.decode("utf-8", errors="replace"))
        return ParsedContent(
            text=text,
            title=_derive_text_title(text),
            source_type="plain_text",
            metadata={"mime_type": normalized_mime_type, "extractor": "plain_text_v1"},
        )

    if normalized_mime_type == "text/html":
        parser = _MinimalHtmlTextExtractor()
        parser.feed(content.decode("utf-8", errors="replace"))
        parser.close()
        text = _normalize_plain_text("".join(parser.text_parts))
        title = _normalize_single_line("".join(parser.title_parts))
        if not text and title:
            text = title
        return ParsedContent(
            text=text,
            title=title or None,
            source_type="web_page",
            metadata={"mime_type": normalized_mime_type, "extractor": "html_text_v1"},
        )

    raise UnsupportedMimeTypeError(normalized_mime_type)


class _MinimalHtmlTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _SUPPRESSED_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self._suppressed_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.lower()
        if normalized_tag in self._SUPPRESSED_TAGS:
            self._suppressed_depth += 1
            return
        if normalized_tag == "title":
            self._in_title = True
        if normalized_tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SUPPRESSED_TAGS and self._suppressed_depth > 0:
            self._suppressed_depth -= 1
            return
        if normalized_tag == "title":
            self._in_title = False
        if normalized_tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)


def _normalize_plain_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_single_line(line) for line in normalized.split("\n")]
    collapsed_lines: list[str] = []
    blank_pending = False
    for line in lines:
        if not line:
            blank_pending = True
            continue
        if blank_pending and collapsed_lines:
            collapsed_lines.append("")
        collapsed_lines.append(line)
        blank_pending = False
    return "\n".join(collapsed_lines).strip()


def _normalize_single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _derive_text_title(text: str) -> str | None:
    first_line = next((line for line in text.split("\n") if line.strip()), "")
    if not first_line:
        return None
    return first_line[:200]
