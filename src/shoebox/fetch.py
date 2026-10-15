"""Get an item's files: the local copy if there is one, otherwise S3 (LOCAL_APP_PLAN.md §8)."""

from __future__ import annotations

import shutil
from pathlib import Path

PRESIGN_SECONDS = 900  # links never live longer than 15 minutes
KINDS = ("compressed", "thumb", "poster", "meta")


def key_for(item: dict, kind: str) -> str | None:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return item.get(f"{kind}_key")


def local_path(library: Path | None, key: str | None) -> Path | None:
    if library is None or not key:
        return None
    p = Path(library) / key
    return p if p.is_file() else None


def presign(bucket, key: str, *, seconds: int = PRESIGN_SECONDS, filename: str | None = None) -> str:
    params = {"Bucket": bucket.name, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return bucket.client.generate_presigned_url("get_object", Params=params, ExpiresIn=min(seconds, PRESIGN_SECONDS))


def fetch(item: dict, kind: str, dest_dir: Path, *, library: Path | None = None, bucket=None) -> Path:
    """Copy the file into dest_dir, from the local library when present, else from S3."""
    key = key_for(item, kind)
    if not key:
        raise LookupError(f"{item['photo_id']} has no {kind} copy")
    dest = Path(dest_dir) / Path(key).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = local_path(library, key)
    if src:
        shutil.copyfile(src, dest)
    elif bucket is not None:
        bucket.download(key, dest)
    else:
        raise LookupError(f"{key} isn't in the local library and no bucket was given")
    return dest
