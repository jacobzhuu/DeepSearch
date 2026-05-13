from __future__ import annotations

from hashlib import sha256

from services.orchestrator.app.acquisition.fetch_outcome import finalize_static_fetch_result
from services.orchestrator.app.acquisition.http_client import HttpFetchResult


def test_finalize_static_fetch_preserves_bytes_for_spa_shell() -> None:
    html = b"""<!doctype html><html><head><script>console.log(1)</script></head>
    <body><div id="app"></div><script src="/bundle.js"></script></body></html>"""
    raw = HttpFetchResult(
        requested_url="https://spa.example/",
        final_url="https://spa.example/",
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=html,
        content_hash=f"sha256:{sha256(html).hexdigest()}",
        trace={"final_url": "https://spa.example/"},
    )
    out = finalize_static_fetch_result(raw)
    assert out.error_code is None
    assert out.content == html
    assert out.content_hash == raw.content_hash
    assert out.trace.get("eligible_for_evidence_parse") is False
    assert out.trace.get("static_html_quality_decision") == "spa_shell"


def test_finalize_static_fetch_marks_normal_html_parse_eligible() -> None:
    html = b"<html><body><p>Enough visible text for a normal article page here.</p></body></html>"
    raw = HttpFetchResult(
        requested_url="https://ok.example/",
        final_url="https://ok.example/",
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=html,
        content_hash=f"sha256:{sha256(html).hexdigest()}",
        trace={},
    )
    out = finalize_static_fetch_result(raw)
    assert out.trace.get("eligible_for_evidence_parse") is True
    assert out.trace.get("static_html_quality_decision") is None
