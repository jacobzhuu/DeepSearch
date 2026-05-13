from __future__ import annotations

from services.orchestrator.app.acquisition.html_quality import (
    evaluate_static_html_quality,
    recommended_soft_fetch_error_code,
)


def test_evaluate_static_html_quality_article_like_page() -> None:
    html = b"""<!doctype html><html><head><title>T</title></head><body>
    <p>First paragraph with enough visible text to exceed heuristic thresholds.</p>
    <p>Second paragraph continues the narrative about systems and evidence pipelines.</p>
    </body></html>"""
    report = evaluate_static_html_quality(html)
    assert report.visible_text_length > 80
    assert report.paragraph_count >= 2
    assert recommended_soft_fetch_error_code(report) is None


def test_evaluate_static_html_quality_spa_shell() -> None:
    html = b"""<!doctype html><html><head><script>console.log(1)</script></head>
    <body><div id="app"></div><script src="/bundle.js"></script></body></html>"""
    report = evaluate_static_html_quality(html)
    assert report.likely_spa_shell is True
    assert recommended_soft_fetch_error_code(report) == "spa_shell"


def test_evaluate_static_html_quality_javascript_required() -> None:
    html = b"""<!doctype html><html><body>
    <noscript>Please enable JavaScript to continue.</noscript>
    </body></html>"""
    report = evaluate_static_html_quality(html)
    assert report.likely_javascript_required is True
    assert recommended_soft_fetch_error_code(report) == "javascript_required"


def test_evaluate_static_html_quality_bot_check() -> None:
    html = b"""<!doctype html><html><body>
    <p>Checking your browser before accessing.</p>
    <script>window._cf_chl_opt={};</script>
    </body></html>"""
    report = evaluate_static_html_quality(html)
    assert report.likely_bot_check is True
    assert recommended_soft_fetch_error_code(report) == "bot_check"
