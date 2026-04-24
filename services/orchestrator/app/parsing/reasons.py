from __future__ import annotations

from enum import StrEnum


class ParseResultReason(StrEnum):
    FETCH_NOT_SUCCEEDED = "fetch_not_succeeded"
    ALREADY_PARSED = "already_parsed"
    SNAPSHOT_OBJECT_MISSING = "snapshot_object_missing"
    UNSUPPORTED_MIME_TYPE = "unsupported_mime_type"
    EMPTY_EXTRACTED_TEXT = "empty_extracted_text"


PARSE_RESULT_REASON_VALUES = tuple(reason.value for reason in ParseResultReason)
