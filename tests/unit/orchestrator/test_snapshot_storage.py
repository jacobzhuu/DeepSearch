from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from minio.error import S3Error
from urllib3 import HTTPResponse

from services.orchestrator.app.storage import (
    FilesystemSnapshotObjectStore,
    MinioSnapshotObjectStore,
    ObjectStoreConfigurationError,
    build_snapshot_object_store,
)


class FakeMinioResponse:
    def __init__(self, content: bytes) -> None:
        self._stream = BytesIO(content)

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        self._stream.close()

    def release_conn(self) -> None:
        return None


class FakeMinioClient:
    def __init__(self, *, buckets: list[str]) -> None:
        self.buckets = set(buckets)
        self.objects: dict[tuple[str, str], bytes] = {}

    def list_buckets(self) -> list[object]:
        return [object() for _ in self.buckets]

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def put_object(
        self,
        bucket: str,
        key: str,
        data: BytesIO,
        length: int,
        content_type: str,
    ) -> None:
        del content_type
        self.objects[(bucket, key)] = data.read(length)

    def get_object(self, bucket: str, key: str) -> FakeMinioResponse:
        try:
            content = self.objects[(bucket, key)]
        except KeyError as error:
            raise _no_such_key_error(bucket, key) from error
        return FakeMinioResponse(content)

    def remove_object(self, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


def test_filesystem_snapshot_store_writes_and_deletes_objects(tmp_path: Path) -> None:
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))

    stored_object = object_store.put_bytes(
        bucket="snapshots",
        key="research-task/task-1/fetch-attempt/attempt-1/response.bin",
        content=b"snapshot-data",
        content_type="text/plain",
    )

    object_path = (
        tmp_path
        / "snapshots"
        / "research-task"
        / "task-1"
        / "fetch-attempt"
        / "attempt-1"
        / "response.bin"
    )
    assert stored_object.bytes_written == len(b"snapshot-data")
    assert object_path.read_bytes() == b"snapshot-data"
    assert object_store.get_bytes(bucket="snapshots", key=stored_object.key) == b"snapshot-data"

    object_store.delete_object(bucket="snapshots", key=stored_object.key)
    assert not object_path.exists()


def test_filesystem_snapshot_store_validate_configuration_creates_root(tmp_path: Path) -> None:
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "nested" / "root"))

    object_store.validate_configuration()

    assert (tmp_path / "nested" / "root").is_dir()


def test_filesystem_snapshot_store_rejects_parent_traversal(tmp_path: Path) -> None:
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))

    with pytest.raises(ValueError):
        object_store.put_bytes(
            bucket="snapshots",
            key="../escape.bin",
            content=b"bad",
            content_type="application/octet-stream",
        )


def test_minio_snapshot_store_writes_reads_and_deletes_objects() -> None:
    fake_client = FakeMinioClient(buckets=["snapshots", "reports"])
    object_store = MinioSnapshotObjectStore(
        endpoint="http://minio.test:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        region=None,
        required_buckets=["snapshots", "reports"],
        client=fake_client,
    )

    object_store.validate_configuration()
    stored_ref = object_store.put_bytes(
        bucket="snapshots",
        key="task-id/v1/report.md",
        content=b"markdown",
        content_type="text/markdown",
    )

    assert stored_ref.bucket == "snapshots"
    assert stored_ref.key == "task-id/v1/report.md"
    assert object_store.get_bytes(bucket="snapshots", key=stored_ref.key) == b"markdown"

    object_store.delete_object(bucket="snapshots", key=stored_ref.key)
    with pytest.raises(FileNotFoundError):
        object_store.get_bytes(bucket="snapshots", key=stored_ref.key)


def test_minio_snapshot_store_validate_configuration_rejects_missing_bucket() -> None:
    object_store = MinioSnapshotObjectStore(
        endpoint="minio.test:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        region=None,
        required_buckets=["snapshots", "reports"],
        client=FakeMinioClient(buckets=["snapshots"]),
    )

    with pytest.raises(ObjectStoreConfigurationError):
        object_store.validate_configuration()


def test_build_snapshot_object_store_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        build_snapshot_object_store(
            backend="invalid-backend",
            root_directory=str(tmp_path),
        )


def _no_such_key_error(bucket: str, key: str) -> S3Error:
    return S3Error(
        code="NoSuchKey",
        message="missing object",
        resource=f"/{bucket}/{key}",
        request_id="request-id",
        host_id="host-id",
        response=HTTPResponse(status=404, body=b""),
        bucket_name=bucket,
        object_name=key,
    )
