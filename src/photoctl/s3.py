"""Thin boto3 wrapper for the archive bucket."""

from __future__ import annotations

from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

TRANSFER = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024)


class Bucket:
    def __init__(self, name: str, profile: str | None = None, client=None):
        self.name = name
        self.client = client or boto3.Session(profile_name=profile).client("s3")

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

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream",
                  metadata: dict[str, str] | None = None) -> None:
        self.client.put_object(Bucket=self.name, Key=key, Body=data, ContentType=content_type,
                               ChecksumAlgorithm="SHA256", ServerSideEncryption="AES256",
                               Metadata={k: _ascii(v) for k, v in (metadata or {}).items()})

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.name, Key=key)["Body"].read()

    def download(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.name, key, str(dest), Config=TRANSFER)

    def list_prefixes(self, prefix: str) -> list[str]:
        """Immediate 'sub-folders' under prefix (which should end in '/')."""
        out: list[str] = []
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.name, Prefix=prefix, Delimiter="/")
        for page in pages:
            out += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
        return out

    def list_keys(self, prefix: str) -> list[str]:
        out: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.name, Prefix=prefix):
            out += [o["Key"] for o in page.get("Contents", [])]
        return out


def _ascii(value: str) -> str:
    # S3 user metadata must be ASCII.
    return value.encode("ascii", "replace").decode("ascii")
