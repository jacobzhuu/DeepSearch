from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from services.orchestrator.app.parsing.extractors import ParsedContent

SUPPORTED_DOCUMENT_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}

SUPPORTED_TEXT_MIME_TYPES = {
    "application/x-env",
    "application/x-yaml",
    "application/yaml",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/x-yaml",
    "text/yaml",
}
SUPPORTED_MIME_TYPES = SUPPORTED_TEXT_MIME_TYPES | set(SUPPORTED_DOCUMENT_MIME_TYPES)

PDF_PAGE_LOCATOR_FALLBACK_REASON = "pdf_page_stream_mapping_unreliable"
PDF_TEXT_FALLBACK_REASON = "pdf_text_operator_extraction_only"
OFFICE_VISUAL_LAYOUT_FALLBACK_REASON = "office_visual_layout_not_interpreted"


class DocumentParseError(ValueError):
    pass


@dataclass(frozen=True)
class _TextSegment:
    text: str
    locator: dict[str, Any]


def extract_document_content(*, mime_type: str, content: bytes) -> ParsedContent:
    normalized_mime_type = normalize_mime_type(mime_type)
    parser_kind = SUPPORTED_DOCUMENT_MIME_TYPES.get(normalized_mime_type)
    if parser_kind == "pdf":
        return _extract_pdf_content(content=content, mime_type=normalized_mime_type)
    if parser_kind == "docx":
        return _extract_docx_content(content=content, mime_type=normalized_mime_type)
    if parser_kind == "pptx":
        return _extract_pptx_content(content=content, mime_type=normalized_mime_type)
    if parser_kind == "xlsx":
        return _extract_xlsx_content(content=content, mime_type=normalized_mime_type)
    raise DocumentParseError(f"unsupported document mime type: {normalized_mime_type}")


def normalize_mime_type(mime_type: str) -> str:
    return mime_type.split(";", 1)[0].strip().lower() or "application/octet-stream"


def mime_policy_metadata(mime_type: str) -> dict[str, Any]:
    normalized_mime_type = normalize_mime_type(mime_type)
    return {
        "mime_type": normalized_mime_type,
        "mime_policy": {
            "supported": normalized_mime_type in SUPPORTED_MIME_TYPES,
            "supported_mime_types": sorted(SUPPORTED_MIME_TYPES),
            "office_macros_executed": False,
            "external_resources_loaded": False,
            "embedded_objects_executed": False,
        },
    }


def _extract_pdf_content(*, content: bytes, mime_type: str) -> ParsedContent:
    if not content.startswith(b"%PDF-"):
        raise DocumentParseError("pdf_signature_mismatch")

    page_count = len(re.findall(rb"/Type\s*/Page\b", content))
    raw_streams = _extract_pdf_streams(content)
    page_texts: list[str] = []
    warnings = [PDF_TEXT_FALLBACK_REASON]
    for stream in raw_streams:
        extracted = _extract_pdf_text_from_stream(stream)
        if extracted:
            page_texts.append(extracted)

    if not page_texts:
        fallback_text = _extract_pdf_literal_text(content)
        if fallback_text:
            page_texts.append(fallback_text)
            warnings.append("pdf_literal_text_fallback")

    locator_reliable = bool(page_count and page_count == len(page_texts))
    if not locator_reliable:
        warnings.append(PDF_PAGE_LOCATOR_FALLBACK_REASON)

    segments = [
        _TextSegment(
            text=text,
            locator={
                "format": "pdf",
                "page_number": index + 1 if locator_reliable else None,
                "page_range": ([index + 1, index + 1] if locator_reliable else None),
                "page_locator_reliable": locator_reliable,
                "locator_fallback_reason": (
                    None if locator_reliable else PDF_PAGE_LOCATOR_FALLBACK_REASON
                ),
            },
        )
        for index, text in enumerate(page_texts)
    ]
    text, segment_payloads = _join_segments(segments)
    return ParsedContent(
        text=text,
        title=_derive_title(text),
        source_type="pdf_document",
        metadata={
            **mime_policy_metadata(mime_type),
            "extractor": "pdf_text_stream_v1",
            "parser_status": "success",
            "parser_kind": "pdf",
            "content_type": mime_type,
            "text_length": len(text),
            "page_count": page_count if page_count else None,
            "page_locator_reliable": locator_reliable,
            "locator_fallback_reason": (
                None if locator_reliable else PDF_PAGE_LOCATOR_FALLBACK_REASON
            ),
            "parser_warnings": warnings,
            "structure_segments": segment_payloads,
        },
    )


def _extract_docx_content(*, content: bytes, mime_type: str) -> ParsedContent:
    with _open_office_zip(content, expected_member="word/document.xml") as archive:
        document_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    segments: list[_TextSegment] = []
    paragraph_no = 0
    for paragraph in _iter_elements(root, "p"):
        text = _normalize_line("".join(node.text or "" for node in _iter_elements(paragraph, "t")))
        if not text:
            continue
        paragraph_no += 1
        segments.append(
            _TextSegment(
                text=text,
                locator={
                    "format": "docx",
                    "paragraph_no": paragraph_no,
                    "section": f"paragraph:{paragraph_no}",
                    "page_locator_reliable": False,
                    "locator_fallback_reason": "docx_has_no_stable_page_numbers",
                },
            )
        )
    text, segment_payloads = _join_segments(segments)
    return ParsedContent(
        text=text,
        title=_derive_title(text),
        source_type="office_document",
        metadata={
            **mime_policy_metadata(mime_type),
            "extractor": "docx_xml_text_v1",
            "parser_status": "success",
            "parser_kind": "docx",
            "content_type": mime_type,
            "text_length": len(text),
            "paragraph_count": len(segments),
            "parser_warnings": [OFFICE_VISUAL_LAYOUT_FALLBACK_REASON],
            "structure_segments": segment_payloads,
        },
    )


def _extract_pptx_content(*, content: bytes, mime_type: str) -> ParsedContent:
    with _open_office_zip(content, expected_member="[Content_Types].xml") as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
            key=_natural_sort_key,
        )
        segments = []
        for slide_no, slide_name in enumerate(slide_names, start=1):
            root = ElementTree.fromstring(archive.read(slide_name))
            text = _normalize_line(
                "\n".join(
                    _normalize_line(node.text or "")
                    for node in _iter_elements(root, "t")
                    if _normalize_line(node.text or "")
                )
            )
            if not text:
                continue
            segments.append(
                _TextSegment(
                    text=text,
                    locator={
                        "format": "pptx",
                        "slide_number": slide_no,
                        "slide_range": [slide_no, slide_no],
                    },
                )
            )
    text, segment_payloads = _join_segments(segments)
    return ParsedContent(
        text=text,
        title=_derive_title(text),
        source_type="office_document",
        metadata={
            **mime_policy_metadata(mime_type),
            "extractor": "pptx_slide_xml_text_v1",
            "parser_status": "success",
            "parser_kind": "pptx",
            "content_type": mime_type,
            "text_length": len(text),
            "slide_count": len(slide_names),
            "parser_warnings": [OFFICE_VISUAL_LAYOUT_FALLBACK_REASON],
            "structure_segments": segment_payloads,
        },
    )


def _extract_xlsx_content(*, content: bytes, mime_type: str) -> ParsedContent:
    with _open_office_zip(content, expected_member="[Content_Types].xml") as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_names = _xlsx_sheet_names(archive)
        worksheet_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
            ),
            key=_natural_sort_key,
        )
        segments = []
        for sheet_index, worksheet_name in enumerate(worksheet_names):
            sheet_name = (
                sheet_names[sheet_index]
                if sheet_index < len(sheet_names)
                else f"Sheet{sheet_index + 1}"
            )
            rows, cell_refs = _xlsx_rows(
                archive.read(worksheet_name),
                shared_strings=shared_strings,
            )
            if not rows:
                continue
            text = _normalize_line("\n".join(rows))
            segments.append(
                _TextSegment(
                    text=text,
                    locator={
                        "format": "xlsx",
                        "sheet_name": sheet_name,
                        "cell_range": _cell_range(cell_refs),
                        "table_block": "used_range",
                    },
                )
            )
    text, segment_payloads = _join_segments(segments)
    return ParsedContent(
        text=text,
        title=_derive_title(text),
        source_type="office_document",
        metadata={
            **mime_policy_metadata(mime_type),
            "extractor": "xlsx_sheet_xml_text_v1",
            "parser_status": "success",
            "parser_kind": "xlsx",
            "content_type": mime_type,
            "text_length": len(text),
            "sheet_count": len(worksheet_names),
            "parser_warnings": [OFFICE_VISUAL_LAYOUT_FALLBACK_REASON],
            "structure_segments": segment_payloads,
        },
    )


def _open_office_zip(content: bytes, *, expected_member: str) -> ZipFile:
    try:
        archive = ZipFile(BytesIO(content))
    except BadZipFile as error:
        raise DocumentParseError("office_zip_signature_mismatch") from error
    if expected_member not in archive.namelist():
        archive.close()
        raise DocumentParseError(f"office_missing_member:{expected_member}")
    return archive


def _extract_pdf_streams(content: bytes) -> list[bytes]:
    streams: list[bytes] = []
    for match in re.finditer(
        rb"<<(?P<dict>.*?)>>\s*stream\r?\n(?P<body>.*?)\r?\nendstream", content, re.S
    ):
        stream_dict = match.group("dict")
        body = match.group("body").strip(b"\r\n")
        if b"/FlateDecode" in stream_dict:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                continue
        streams.append(body)
    return streams


def _extract_pdf_text_from_stream(stream: bytes) -> str:
    parts: list[str] = []
    for raw_text in re.findall(rb"\((?:\\.|[^\\()])*\)\s*Tj", stream):
        parts.append(_decode_pdf_string(raw_text[:-2].strip()))
    for raw_array in re.findall(rb"\[(.*?)\]\s*TJ", stream, flags=re.S):
        strings = re.findall(rb"\((?:\\.|[^\\()])*\)", raw_array)
        if strings:
            parts.append("".join(_decode_pdf_string(value) for value in strings))
    return _normalize_text("\n".join(part for part in parts if part.strip()))


def _extract_pdf_literal_text(content: bytes) -> str:
    parts = [_decode_pdf_string(value) for value in re.findall(rb"\((?:\\.|[^\\()])*\)", content)]
    return _normalize_text("\n".join(part for part in parts if len(part.strip()) >= 3))


def _decode_pdf_string(value: bytes) -> str:
    raw = value.strip()
    if raw.startswith(b"(") and raw.endswith(b")"):
        raw = raw[1:-1]
    raw = re.sub(rb"\\([nrtbf()\\])", _pdf_escape_replacement, raw)
    raw = re.sub(rb"\\([0-7]{1,3})", lambda match: bytes([int(match.group(1), 8)]), raw)
    return (
        raw.decode("utf-8", errors="replace")
        .encode("latin-1", errors="ignore")
        .decode("latin-1", errors="replace")
    )


def _pdf_escape_replacement(match: re.Match[bytes]) -> bytes:
    value = match.group(1)
    replacements = {
        b"n": b"\n",
        b"r": b"\r",
        b"t": b"\t",
        b"b": b"\b",
        b"f": b"\f",
        b"(": b"(",
        b")": b")",
        b"\\": b"\\",
    }
    return replacements.get(value, value)


def _iter_elements(root: ElementTree.Element, local_name: str) -> list[ElementTree.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == local_name]


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in _iter_elements(root, "si"):
        strings.append(
            _normalize_line("".join(node.text or "" for node in _iter_elements(item, "t")))
        )
    return strings


def _xlsx_sheet_names(archive: ZipFile) -> list[str]:
    if "xl/workbook.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    return [
        str(sheet.attrib.get("name"))
        for sheet in _iter_elements(root, "sheet")
        if sheet.attrib.get("name")
    ]


def _xlsx_rows(worksheet_xml: bytes, *, shared_strings: list[str]) -> tuple[list[str], list[str]]:
    root = ElementTree.fromstring(worksheet_xml)
    rows: list[str] = []
    cell_refs: list[str] = []
    for row in _iter_elements(root, "row"):
        cells: list[str] = []
        for cell in _iter_elements(row, "c"):
            cell_ref = str(cell.attrib.get("r") or "")
            value = _xlsx_cell_text(cell, shared_strings=shared_strings)
            if not value:
                continue
            if cell_ref:
                cell_refs.append(cell_ref)
                cells.append(f"{cell_ref}: {value}")
            else:
                cells.append(value)
        if cells:
            rows.append(" | ".join(cells))
    return rows, cell_refs


def _xlsx_cell_text(cell: ElementTree.Element, *, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value_node = next((child for child in cell if _local_name(child.tag) == "v"), None)
    if value_node is not None and value_node.text is not None:
        raw_value = value_node.text.strip()
        if cell_type == "s":
            try:
                return shared_strings[int(raw_value)]
            except (ValueError, IndexError):
                return raw_value
        return raw_value
    inline = next((child for child in cell if _local_name(child.tag) == "is"), None)
    if inline is not None:
        return _normalize_line("".join(node.text or "" for node in _iter_elements(inline, "t")))
    return ""


def _cell_range(cell_refs: list[str]) -> str | None:
    if not cell_refs:
        return None
    return f"{cell_refs[0]}:{cell_refs[-1]}"


def _join_segments(segments: list[_TextSegment]) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    payloads: list[dict[str, Any]] = []
    cursor = 0
    for segment in segments:
        text = _normalize_text(segment.text)
        if not text:
            continue
        if parts:
            parts.append("")
            cursor += 2
        start = cursor
        parts.append(text)
        cursor += len(text)
        payloads.append({"start_offset": start, "end_offset": cursor, **segment.locator})
    return "\n\n".join(parts).strip(), payloads


def _derive_title(text: str) -> str | None:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:200] if first_line else None


def _normalize_text(text: str) -> str:
    lines = [
        _normalize_line(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    return "\n".join(line for line in lines if line).strip()


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _natural_sort_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value))
