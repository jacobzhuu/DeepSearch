#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

from minio import Minio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create DeepSearch object-store buckets.")
    parser.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", ""))
    parser.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", ""))
    parser.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", ""))
    parser.add_argument(
        "--secure",
        action="store_true",
        default=_get_env_bool("MINIO_SECURE", False),
    )
    parser.add_argument("--region", default=os.getenv("MINIO_REGION", ""))
    parser.add_argument(
        "--bucket",
        dest="buckets",
        action="append",
        default=[],
        help="Bucket to ensure exists. Can be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    normalized_endpoint, secure = normalize_endpoint(args.endpoint, secure=args.secure)
    buckets = args.buckets or _default_buckets()
    if not buckets:
        raise SystemExit("at least one bucket must be configured")

    client = Minio(
        normalized_endpoint,
        access_key=args.access_key,
        secret_key=args.secret_key,
        secure=secure,
        region=args.region.strip() or None,
    )

    created: list[str] = []
    existing: list[str] = []
    for bucket in buckets:
        normalized_bucket = bucket.strip().strip("/")
        if not normalized_bucket:
            continue
        if client.bucket_exists(normalized_bucket):
            existing.append(normalized_bucket)
            continue
        client.make_bucket(normalized_bucket)
        created.append(normalized_bucket)

    print(
        json.dumps(
            {
                "endpoint": normalized_endpoint,
                "secure": secure,
                "created": created,
                "existing": existing,
            },
            ensure_ascii=True,
        )
    )
    return 0


def normalize_endpoint(endpoint: str, *, secure: bool) -> tuple[str, bool]:
    stripped = endpoint.strip()
    if not stripped:
        raise SystemExit("MINIO_ENDPOINT must not be empty")
    parsed = urlsplit(stripped)
    if parsed.scheme in {"http", "https"}:
        if not parsed.netloc:
            raise SystemExit(f"invalid MINIO endpoint: {endpoint}")
        return parsed.netloc, parsed.scheme == "https"
    if "://" in stripped:
        raise SystemExit(f"unsupported MINIO endpoint scheme: {endpoint}")
    return stripped, secure


def _default_buckets() -> list[str]:
    buckets: list[str] = []
    for env_name in ("SNAPSHOT_STORAGE_BUCKET", "REPORT_STORAGE_BUCKET"):
        value = os.getenv(env_name, "").strip()
        if value:
            buckets.append(value)
    return buckets


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
