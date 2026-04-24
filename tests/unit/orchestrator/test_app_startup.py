from __future__ import annotations

import pytest

from services.orchestrator.app.main import create_app
from services.orchestrator.app.settings import get_settings


def test_create_app_fails_for_unsupported_snapshot_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNAPSHOT_STORAGE_BACKEND", "invalid-backend")
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        monkeypatch.delenv("SNAPSHOT_STORAGE_BACKEND", raising=False)
        get_settings.cache_clear()


def test_create_app_fails_for_invalid_minio_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNAPSHOT_STORAGE_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ENDPOINT", "")
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        monkeypatch.delenv("SNAPSHOT_STORAGE_BACKEND", raising=False)
        monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
        get_settings.cache_clear()


def test_create_app_fails_for_unsupported_index_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INDEX_BACKEND", "invalid-backend")
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        monkeypatch.delenv("INDEX_BACKEND", raising=False)
        get_settings.cache_clear()


def test_create_app_fails_when_opensearch_connectivity_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingBackend:
        def validate_configuration(self) -> None:
            raise RuntimeError("backend connectivity failed")

    monkeypatch.setenv("OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP", "true")
    monkeypatch.setattr(
        "services.orchestrator.app.main.build_chunk_index_backend",
        lambda **_: FailingBackend(),
    )
    get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError):
            create_app()
    finally:
        monkeypatch.delenv("OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP", raising=False)
        get_settings.cache_clear()
