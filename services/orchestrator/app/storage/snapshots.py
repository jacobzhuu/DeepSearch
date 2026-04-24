from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True)
class StoredObjectRef:
    bucket: str
    key: str
    bytes_written: int


class ObjectStoreConfigurationError(RuntimeError):
    pass


class ObjectStoreOperationError(RuntimeError):
    pass


class SnapshotObjectStore(Protocol):
    def put_bytes(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObjectRef: ...

    def delete_object(self, *, bucket: str, key: str) -> None: ...

    def get_bytes(self, *, bucket: str, key: str) -> bytes: ...

    def validate_configuration(self) -> None: ...


class FilesystemSnapshotObjectStore:
    def __init__(self, *, root_directory: str) -> None:
        self.root_directory = Path(root_directory)

    def put_bytes(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObjectRef:
        del content_type
        object_path = self._resolve_object_path(bucket=bucket, key=key)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(content)
        return StoredObjectRef(
            bucket=bucket,
            key=key,
            bytes_written=len(content),
        )

    def delete_object(self, *, bucket: str, key: str) -> None:
        object_path = self._resolve_object_path(bucket=bucket, key=key)
        object_path.unlink(missing_ok=True)

    def get_bytes(self, *, bucket: str, key: str) -> bytes:
        object_path = self._resolve_object_path(bucket=bucket, key=key)
        return object_path.read_bytes()

    def validate_configuration(self) -> None:
        self.root_directory.mkdir(parents=True, exist_ok=True)
        if not self.root_directory.is_dir():
            raise ObjectStoreConfigurationError(
                f"snapshot storage root is not a directory: {self.root_directory}"
            )

    def _resolve_object_path(self, *, bucket: str, key: str) -> Path:
        normalized_bucket = _normalize_bucket(bucket)
        normalized_key = _normalize_key(key)
        return self.root_directory / normalized_bucket / Path(*normalized_key.parts)


class MinioSnapshotObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        region: str | None,
        required_buckets: list[str] | None = None,
        client: Any | None = None,
    ) -> None:
        normalized_endpoint, normalized_secure = _normalize_minio_endpoint(endpoint, secure=secure)
        self.endpoint = normalized_endpoint
        self.access_key = access_key.strip()
        self.secret_key = secret_key.strip()
        self.secure = normalized_secure
        self.region = region.strip() if region is not None and region.strip() else None
        self.required_buckets = [_normalize_bucket(bucket) for bucket in required_buckets or []]
        self.client = client or Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            region=self.region,
        )

    def put_bytes(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObjectRef:
        normalized_bucket = _normalize_bucket(bucket)
        normalized_key = _normalize_key_string(key)
        try:
            self.client.put_object(
                normalized_bucket,
                normalized_key,
                BytesIO(content),
                length=len(content),
                content_type=content_type,
            )
        except S3Error as error:
            raise ObjectStoreOperationError(
                f"failed to store object {normalized_bucket}/{normalized_key}: {error.code}"
            ) from error
        except Exception as error:
            raise ObjectStoreOperationError(
                f"failed to store object {normalized_bucket}/{normalized_key}: {error}"
            ) from error
        return StoredObjectRef(
            bucket=normalized_bucket,
            key=normalized_key,
            bytes_written=len(content),
        )

    def delete_object(self, *, bucket: str, key: str) -> None:
        normalized_bucket = _normalize_bucket(bucket)
        normalized_key = _normalize_key_string(key)
        try:
            self.client.remove_object(normalized_bucket, normalized_key)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                return
            raise ObjectStoreOperationError(
                f"failed to delete object {normalized_bucket}/{normalized_key}: {error.code}"
            ) from error
        except Exception as error:
            raise ObjectStoreOperationError(
                f"failed to delete object {normalized_bucket}/{normalized_key}: {error}"
            ) from error

    def get_bytes(self, *, bucket: str, key: str) -> bytes:
        normalized_bucket = _normalize_bucket(bucket)
        normalized_key = _normalize_key_string(key)
        response: Any | None = None
        try:
            response = self.client.get_object(normalized_bucket, normalized_key)
            return bytes(response.read())
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                raise FileNotFoundError(f"{normalized_bucket}/{normalized_key}") from error
            raise ObjectStoreOperationError(
                f"failed to read object {normalized_bucket}/{normalized_key}: {error.code}"
            ) from error
        except Exception as error:
            raise ObjectStoreOperationError(
                f"failed to read object {normalized_bucket}/{normalized_key}: {error}"
            ) from error
        finally:
            _close_response(response)

    def validate_configuration(self) -> None:
        if not self.access_key:
            raise ObjectStoreConfigurationError("minio access key must not be empty")
        if not self.secret_key:
            raise ObjectStoreConfigurationError("minio secret key must not be empty")
        try:
            if self.required_buckets:
                for bucket in self.required_buckets:
                    if not self.client.bucket_exists(bucket):
                        raise ObjectStoreConfigurationError(
                            f"required MinIO bucket does not exist: {bucket}"
                        )
            else:
                self.client.list_buckets()
        except ObjectStoreConfigurationError:
            raise
        except S3Error as error:
            raise ObjectStoreConfigurationError(
                f"failed to validate MinIO configuration against {self.endpoint}: {error.code}"
            ) from error
        except Exception as error:
            raise ObjectStoreConfigurationError(
                f"failed to validate MinIO configuration against {self.endpoint}: {error}"
            ) from error


def build_snapshot_object_store(
    *,
    backend: str,
    root_directory: str,
    minio_endpoint: str = "",
    minio_access_key: str = "",
    minio_secret_key: str = "",
    minio_secure: bool = False,
    minio_region: str | None = None,
    required_buckets: list[str] | None = None,
) -> SnapshotObjectStore:
    normalized_backend = backend.strip().lower()
    if normalized_backend == "filesystem":
        return FilesystemSnapshotObjectStore(root_directory=root_directory)
    if normalized_backend == "minio":
        return MinioSnapshotObjectStore(
            endpoint=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            secure=minio_secure,
            region=minio_region,
            required_buckets=required_buckets,
        )

    raise ObjectStoreConfigurationError(f"unsupported snapshot storage backend: {backend}")


def _normalize_bucket(bucket: str) -> str:
    normalized_bucket = bucket.strip().strip("/")
    if not normalized_bucket:
        raise ValueError("bucket must not be empty")
    return normalized_bucket


def _normalize_key(key: str) -> PurePosixPath:
    normalized_key = PurePosixPath(key.strip())
    if not str(normalized_key) or normalized_key.is_absolute():
        raise ValueError("key must be a relative object path")
    if ".." in normalized_key.parts:
        raise ValueError("key must not contain parent-directory traversal")
    return normalized_key


def _normalize_key_string(key: str) -> str:
    return str(_normalize_key(key))


def _normalize_minio_endpoint(endpoint: str, *, secure: bool) -> tuple[str, bool]:
    stripped = endpoint.strip()
    if not stripped:
        raise ObjectStoreConfigurationError("minio endpoint must not be empty")

    if stripped.startswith(("http://", "https://")):
        parsed = urlsplit(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ObjectStoreConfigurationError(f"invalid MinIO endpoint: {endpoint}")
        return parsed.netloc, parsed.scheme == "https"

    if "://" in stripped or "/" in stripped:
        raise ObjectStoreConfigurationError(f"invalid MinIO endpoint: {endpoint}")
    return stripped, secure


def _close_response(response: Any | None) -> None:
    if response is None:
        return
    close = getattr(response, "close", None)
    if callable(close):
        close()
    release_conn = getattr(response, "release_conn", None)
    if callable(release_conn):
        release_conn()
