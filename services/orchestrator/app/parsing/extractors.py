from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
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
        decoded_content = content.decode("utf-8", errors="replace")
        parser.feed(decoded_content)
        parser.close()
        main_text = _normalize_plain_text(parser.main_text)
        all_text = _normalize_plain_text(parser.all_text)
        text = main_text or all_text
        title = _normalize_single_line("".join(parser.title_parts))
        extractor_strategy_used = "main_content"
        fallback_used = False
        if _looks_like_mediawiki_html(decoded_content):
            fallback_text, fallback_strategy = _extract_mediawiki_fallback_text(decoded_content)
            if fallback_text and (not main_text or len(fallback_text) >= max(120, len(text) * 2)):
                text = fallback_text
                extractor_strategy_used = fallback_strategy
                fallback_used = True
        if not text and title:
            text = title
            extractor_strategy_used = "title_text_fallback"
            fallback_used = True
        text, dropped_broken_link_fragments = _cleanup_broken_link_fragments(text)
        metadata: dict[str, object] = {
            "mime_type": normalized_mime_type,
            "extractor": "html_main_content_v1",
            "extractor_fallback": parser.extractor_fallback,
            "extractor_strategy_used": extractor_strategy_used,
            "fallback_used": fallback_used,
            "removed_boilerplate_count": parser.removed_boilerplate_count,
            "extracted_text_length": len(text),
            "text_cleanup_applied": bool(dropped_broken_link_fragments),
            "dropped_broken_link_fragments": dropped_broken_link_fragments,
            "preserved_link_text_count": parser.preserved_link_text_count,
            "link_text_extraction_strategy": "html_parser_data_nodes",
        }
        redirect_stub = _detect_redirect_stub(text=text, raw_html=decoded_content)
        if redirect_stub is not None:
            metadata.update(redirect_stub)
        return ParsedContent(
            text=text,
            title=title or None,
            source_type="web_page",
            metadata=metadata,
        )

    raise UnsupportedMimeTypeError(normalized_mime_type)


class _MinimalHtmlTextExtractor(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
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
    _SUPPRESSED_TAGS = {
        "aside",
        "button",
        "footer",
        "form",
        "header",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
    }
    _MAIN_TAGS = {"article", "main"}
    _SUPPRESSED_CLASS_MARKERS = (
        "ambox",
        "catlinks",
        "cookie",
        "footer",
        "hatnote",
        "mw-editsection",
        "mw-jump-link",
        "mw-references",
        "navbox",
        "navigation",
        "reflist",
        "sidebar",
        "vector-header",
    )
    _SUPPRESSED_CLASS_TOKENS = {
        "menu",
        "mw-editsection",
        "mw-jump-link",
        "navbox",
        "reference",
        "references",
        "reflist",
        "sidebar",
        "toc",
    }
    _MAIN_ID_MARKERS = {"bodycontent", "content", "main", "mw-content-text"}
    _MAIN_CLASS_MARKERS = {"mw-body", "mw-parser-output"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.all_text_parts: list[str] = []
        self.main_text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.removed_boilerplate_count = 0
        self._element_depth = 0
        self._suppressed_depth = 0
        self._suppressed_stack: list[int] = []
        self._main_depth = 0
        self._main_stack: list[int] = []
        self._in_title = False
        self._anchor_depth = 0
        self._anchor_parts: list[str] = []
        self.preserved_link_text_count = 0

    @property
    def all_text(self) -> str:
        return "".join(self.all_text_parts)

    @property
    def main_text(self) -> str:
        normalized_main = _normalize_plain_text("".join(self.main_text_parts))
        if len(normalized_main) >= 40:
            return normalized_main
        return ""

    @property
    def extractor_fallback(self) -> str | None:
        return None if self.main_text else "full_document_text"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attrs_by_name = {name.lower(): (value or "").strip().lower() for name, value in attrs}
        if normalized_tag in self._VOID_TAGS:
            if normalized_tag in self._BLOCK_TAGS:
                self._append_text("\n")
            return
        self._element_depth += 1
        if self._suppressed_depth > 0:
            self._suppressed_depth += 1
            self._suppressed_stack.append(self._element_depth)
            return
        if normalized_tag in self._SUPPRESSED_TAGS or self._is_suppressed_attrs(attrs_by_name):
            self._suppressed_depth += 1
            self._suppressed_stack.append(self._element_depth)
            self.removed_boilerplate_count += 1
            return
        if normalized_tag == "title":
            self._in_title = True
        if normalized_tag == "a":
            self._anchor_depth += 1
            if self._anchor_depth == 1:
                self._anchor_parts = []
        if self._is_main_start(normalized_tag, attrs_by_name):
            self._main_depth += 1
            self._main_stack.append(self._element_depth)
        if normalized_tag in self._BLOCK_TAGS:
            self._append_text("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._suppressed_depth > 0 and self._suppressed_stack:
            self._suppressed_depth -= 1
            self._suppressed_stack.pop()
            self._element_depth = max(0, self._element_depth - 1)
            return
        if normalized_tag == "title":
            self._in_title = False
        if normalized_tag == "a" and self._anchor_depth > 0:
            if self._anchor_depth == 1 and _normalize_single_line("".join(self._anchor_parts)):
                self.preserved_link_text_count += 1
            self._anchor_depth -= 1
            if self._anchor_depth == 0:
                self._anchor_parts = []
        if normalized_tag in self._BLOCK_TAGS:
            self._append_text("\n")
        if (
            self._main_depth > 0
            and self._main_stack
            and self._main_stack[-1] == self._element_depth
        ):
            self._main_depth -= 1
            self._main_stack.pop()
        self._element_depth = max(0, self._element_depth - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._anchor_depth > 0:
            self._anchor_parts.append(data)
        self._append_text(data)

    def _append_text(self, value: str) -> None:
        self.all_text_parts.append(value)
        if self._main_depth > 0:
            self.main_text_parts.append(value)

    def _is_main_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag in self._MAIN_TAGS or attrs.get("role") == "main":
            return True
        element_id = attrs.get("id", "")
        normalized_id = element_id.replace("-", "").lower()
        if element_id in self._MAIN_ID_MARKERS or normalized_id in self._MAIN_ID_MARKERS:
            return True
        class_names = set(attrs.get("class", "").split())
        return bool(class_names.intersection(self._MAIN_CLASS_MARKERS))

    def _is_suppressed_attrs(self, attrs: dict[str, str]) -> bool:
        role = attrs.get("role", "")
        if role in {"navigation", "banner", "contentinfo", "complementary", "search"}:
            return True
        class_tokens = set(attrs.get("class", "").split())
        if class_tokens.intersection(self._SUPPRESSED_CLASS_TOKENS):
            return True
        element_id = attrs.get("id", "")
        if element_id == "toc" or element_id.startswith("toc-"):
            return True
        combined = " ".join(
            value for key, value in attrs.items() if key in {"id", "class", "aria-label"}
        )
        return any(marker in combined for marker in self._SUPPRESSED_CLASS_MARKERS)


class _ParagraphTextExtractor(HTMLParser):
    _VOID_TAGS = _MinimalHtmlTextExtractor._VOID_TAGS
    _SUPPRESSED_TAGS = _MinimalHtmlTextExtractor._SUPPRESSED_TAGS
    _SUPPRESSED_CLASS_MARKERS = _MinimalHtmlTextExtractor._SUPPRESSED_CLASS_MARKERS

    def __init__(
        self,
        *,
        target_id: str | None = None,
        target_class: str | None = None,
        body_only: bool = False,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.target_class = target_class
        self.body_only = body_only
        self.paragraphs: list[str] = []
        self.removed_boilerplate_count = 0
        self._element_depth = 0
        self._target_depth = 0
        self._target_stack: list[int] = []
        self._suppressed_depth = 0
        self._suppressed_stack: list[int] = []
        self._paragraph_depth = 0
        self._paragraph_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attrs_by_name = {name.lower(): (value or "").strip().lower() for name, value in attrs}
        if normalized_tag in self._VOID_TAGS:
            return
        self._element_depth += 1
        if self._suppressed_depth > 0:
            self._suppressed_depth += 1
            self._suppressed_stack.append(self._element_depth)
            return
        if normalized_tag in self._SUPPRESSED_TAGS or self._is_suppressed_attrs(attrs_by_name):
            self._suppressed_depth += 1
            self._suppressed_stack.append(self._element_depth)
            self.removed_boilerplate_count += 1
            return
        if self._is_target_start(normalized_tag, attrs_by_name):
            self._target_depth += 1
            self._target_stack.append(self._element_depth)
        if normalized_tag == "p" and self._target_depth > 0:
            self._paragraph_depth = self._element_depth
            self._paragraph_parts = []

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._suppressed_depth > 0 and self._suppressed_stack:
            self._suppressed_depth -= 1
            self._suppressed_stack.pop()
            self._element_depth = max(0, self._element_depth - 1)
            return
        if (
            normalized_tag == "p"
            and self._paragraph_depth > 0
            and self._paragraph_depth == self._element_depth
        ):
            paragraph = _normalize_single_line("".join(self._paragraph_parts))
            if _is_readable_paragraph(paragraph):
                self.paragraphs.append(paragraph)
            self._paragraph_depth = 0
            self._paragraph_parts = []
        if (
            self._target_depth > 0
            and self._target_stack
            and self._target_stack[-1] == self._element_depth
        ):
            self._target_depth -= 1
            self._target_stack.pop()
        self._element_depth = max(0, self._element_depth - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth > 0:
            return
        if self._paragraph_depth > 0:
            self._paragraph_parts.append(data)

    def _is_target_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if self.body_only and tag == "body":
            return True
        if self.target_id is not None and attrs.get("id") == self.target_id:
            return True
        if self.target_class is not None:
            class_names = set(attrs.get("class", "").split())
            return self.target_class in class_names
        return False

    def _is_suppressed_attrs(self, attrs: dict[str, str]) -> bool:
        role = attrs.get("role", "")
        if role in {"navigation", "banner", "contentinfo", "complementary", "search"}:
            return True
        class_tokens = set(attrs.get("class", "").split())
        if class_tokens.intersection(_MinimalHtmlTextExtractor._SUPPRESSED_CLASS_TOKENS):
            return True
        element_id = attrs.get("id", "")
        if element_id == "toc" or element_id.startswith("toc-"):
            return True
        combined = " ".join(
            value for key, value in attrs.items() if key in {"id", "class", "aria-label"}
        )
        return any(marker in combined for marker in self._SUPPRESSED_CLASS_MARKERS)


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


def _cleanup_broken_link_fragments(text: str) -> tuple[str, list[str]]:
    dropped: list[str] = []
    cleaned = text
    replacements = (
        (r"\s+from\s+up\s+to\s+\d+\s+\.", ".", "from up to <number> ."),
        (r"\s+listed\s+at\s+\.", ".", "listed at ."),
        (r"\s+see\s+\.", ".", "see ."),
        (r"\s+at\s+\.", ".", "at ."),
    )
    for pattern, replacement, label in replacements:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            dropped.append(label)
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    if dropped:
        cleaned = _normalize_plain_text(cleaned)
    return cleaned, dropped


def _looks_like_mediawiki_html(raw_html: str) -> bool:
    lower = raw_html.lower()
    return "mw-parser-output" in lower or "mw-content-text" in lower or "mediawiki" in lower


def _extract_mediawiki_fallback_text(raw_html: str) -> tuple[str, str]:
    strategies: tuple[tuple[str, dict[str, object]], ...] = (
        ("wikipedia_mw_parser_output_paragraphs", {"target_class": "mw-parser-output"}),
        ("wikipedia_mw_content_text_paragraphs", {"target_id": "mw-content-text"}),
        ("body_readable_paragraphs", {"body_only": True}),
    )
    for strategy_name, options in strategies:
        extractor = _ParagraphTextExtractor(**options)
        extractor.feed(raw_html)
        extractor.close()
        text = _normalize_plain_text("\n\n".join(extractor.paragraphs))
        if text:
            return text, strategy_name
    return "", "mediawiki_no_fallback_text"


def _is_readable_paragraph(paragraph: str) -> bool:
    if len(paragraph) < 40:
        return False
    lower = paragraph.lower()
    if any(
        phrase in lower
        for phrase in (
            "jump to content",
            "main menu",
            "move to sidebar",
            "privacy policy",
            "about wikipedia",
            "retrieved from",
            "hidden categories",
        )
    ):
        return False
    return any(mark in paragraph for mark in (".", "!", "?", "。", "！", "？"))


def _detect_redirect_stub(*, text: str, raw_html: str) -> dict[str, object] | None:
    normalized = _normalize_single_line(text)
    lower = normalized.lower()
    if len(normalized) > 500:
        return None
    redirect_markers = (
        "redirecting to ",
        "you are being redirected",
        "moved permanently",
        "click here if you are not redirected",
    )
    if not any(marker in lower for marker in redirect_markers):
        return None

    target_url = _extract_redirect_target(normalized) or _extract_redirect_target(raw_html)
    metadata: dict[str, object] = {
        "content_quality": "low",
        "reason": "redirect_stub",
        "should_generate_claims": False,
    }
    if target_url is not None:
        metadata["discovered_followup_url"] = target_url
    return metadata


def _extract_redirect_target(value: str) -> str | None:
    patterns = (
        r"https?://[^\s<>'\")]+",
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"']*url=([^\"'>]+)",
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match is None:
            continue
        target = match.group(1) if match.lastindex else match.group(0)
        target = unescape(target).strip().rstrip(".,;")
        if target.startswith(("http://", "https://")):
            return target
    return None
