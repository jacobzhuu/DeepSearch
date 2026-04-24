#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic SearXNG-compatible mock.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    return parser.parse_args()


class MockSearXNGHandler(BaseHTTPRequestHandler):
    server_version = "MockSearXNG/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path != "/search":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        format_value = params.get("format", ["json"])[0]
        if format_value != "json":
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "format must be json"})
            return

        payload = {
            "query": query,
            "number_of_results": 2,
            "results": [
                {
                    "url": "https://example.com/",
                    "title": "Example Domain",
                    "content": "This domain is for use in illustrative examples in documents.",
                    "engine": "mock",
                    "category": "general",
                    "score": 1.0,
                },
                {
                    "url": "https://example.com/?utm_source=smoke",
                    "title": "Example Domain Duplicate",
                    "content": "Duplicate result used to exercise canonical dedupe.",
                    "engine": "mock",
                    "category": "general",
                    "score": 0.9,
                },
            ],
            "query_correction": None,
        }
        self._write_json(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MockSearXNGHandler)
    print(
        json.dumps(
            {"host": args.host, "port": args.port, "base_url": f"http://{args.host}:{args.port}"},
            ensure_ascii=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
