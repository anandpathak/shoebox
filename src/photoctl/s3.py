"""Thin boto3 wrapper for the archive bucket."""

from __future__ import annotations

from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

TRANSFER = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024)


class Bucket:
    def __init__(self, name: str, profile: str | None = None):
        session = boto3.Session(profile_name=profile)
        self.name = name
        self.client = session.client("s3")

    def head(self, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.name, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def upload(
        self,
        path: Path,
        key: str,
        *,
        storage_class: str = "STANDARD",
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        extra = {
            "StorageClass": storage_class,
            "ChecksumAlgorithm": "SHA256",  # S3 verifies the bytes it received
            "ServerSideEncryption": "AES256",
            "Metadata": {k: _ascii(v) for k, v in (metadata or {}).items()},
        }
        if content_type:
            extra["ContentType"] = content_type
        self.client.upload_file(str(path), self.name, key, ExtraArgs=extra, Config=TRANSFER)
        head = self.head(key)
        if head is None or head["ContentLength"] != path.stat().st_size:
            raise RuntimeError(f"upload verification failed for s3://{self.name}/{key}")


def _ascii(value: str) -> str:
    # S3 user metadata must be ASCII.
    return value.encode("ascii", "replace").decode("ascii")
