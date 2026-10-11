"""Thin boto3 wrapper for the archive bucket."""

from __future__ import annotations

import time
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

MULTIPART_THRESHOLD = 64 * 1024 * 1024
PART_SIZE = 16 * 1024 * 1024
TRANSFER = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD, multipart_chunksize=PART_SIZE)


def _code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


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

    def create_if_absent(self, path: Path, key: str, *, storage_class: str = "STANDARD",
                         content_type: str | None = None, metadata: dict[str, str] | None = None) -> bool:
        """Upload only if `key` doesn't exist yet (S3 If-None-Match). True if we created it, False if it was
        already there — e.g. another device archived the same photo at the same moment."""
        extra = {"StorageClass": storage_class, "ServerSideEncryption": "AES256", "ChecksumAlgorithm": "SHA256",
                 "Metadata": {k: _ascii(v) for k, v in (metadata or {}).items()}}
        if content_type:
            extra["ContentType"] = content_type
        size = path.stat().st_size
        for attempt in range(5):
            try:
                created = (self._put_if_absent(path, key, extra) if size <= MULTIPART_THRESHOLD
                           else self._multipart_if_absent(path, key, extra, size))
            except ClientError as e:
                if _code(e) == "ConditionalRequestConflict":  # a concurrent conditional write is in flight
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
            if created:
                head = self.head(key)
                if head is None or head["ContentLength"] != size:
                    raise RuntimeError(f"upload verification failed for s3://{self.name}/{key}")
            return created
        raise RuntimeError(f"gave up after repeated conflicting writes to s3://{self.name}/{key}")

    def _put_if_absent(self, path: Path, key: str, extra: dict) -> bool:
        try:
            with open(path, "rb") as f:
                self.client.put_object(Bucket=self.name, Key=key, Body=f, IfNoneMatch="*", **extra)
            return True
        except ClientError as e:
            if _code(e) == "PreconditionFailed":
                return False
            raise

    def _multipart_if_absent(self, path: Path, key: str, extra: dict, size: int) -> bool:
        if self.head(key) is not None:  # cheap early exit before sending gigabytes
            return False
        upload_id = self.client.create_multipart_upload(Bucket=self.name, Key=key, **extra)["UploadId"]
        try:
            parts = []
            with open(path, "rb") as f:
                for number in range(1, (size + PART_SIZE - 1) // PART_SIZE + 1):
                    resp = self.client.upload_part(Bucket=self.name, Key=key, UploadId=upload_id, PartNumber=number,
                                                   Body=f.read(PART_SIZE), ChecksumAlgorithm="SHA256")
                    parts.append({"PartNumber": number, "ETag": resp["ETag"],
                                  "ChecksumSHA256": resp.get("ChecksumSHA256")})
            parts = [{k: v for k, v in p.items() if v is not None} for p in parts]
            self.client.complete_multipart_upload(Bucket=self.name, Key=key, UploadId=upload_id,
                                                  MultipartUpload={"Parts": parts}, IfNoneMatch="*")
            return True
        except ClientError as e:
            self.client.abort_multipart_upload(Bucket=self.name, Key=key, UploadId=upload_id)
            if _code(e) == "PreconditionFailed":
                return False
            raise
        except BaseException:
            self.client.abort_multipart_upload(Bucket=self.name, Key=key, UploadId=upload_id)
            raise

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
