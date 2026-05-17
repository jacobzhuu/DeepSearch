from __future__ import annotations


class StructuredSynthesisValidationError(ValueError):
    """Raised when LLM JSON fails post-parse validation (caller should fall back)."""
