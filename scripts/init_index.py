#!/usr/bin/env python3
from __future__ import annotations

import json

from services.orchestrator.app.indexing import build_chunk_index_backend
from services.orchestrator.app.settings import get_settings


def main() -> int:
    settings = get_settings()
    backend = build_chunk_index_backend(
        backend=settings.index_backend,
        opensearch_base_url=settings.opensearch_base_url,
        opensearch_index_name=settings.opensearch_index_name,
        opensearch_username=settings.opensearch_username,
        opensearch_password=settings.opensearch_password,
        opensearch_verify_tls=settings.opensearch_verify_tls,
        opensearch_ca_bundle_path=settings.opensearch_ca_bundle_path,
        opensearch_timeout_seconds=settings.opensearch_timeout_seconds,
        opensearch_validate_connectivity=settings.opensearch_validate_connectivity_on_startup,
    )
    backend.validate_configuration()
    backend.ensure_index()
    print(
        json.dumps(
            {
                "backend": settings.index_backend,
                "index_name": settings.opensearch_index_name,
                "opensearch_base_url": settings.opensearch_base_url,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
