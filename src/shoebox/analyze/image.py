"""Load the picture to analyse: the 1600px compressed copy (or a video's poster frame), upright."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1600


def source_path(library: Path, row) -> Path | None:
    """The local file for a catalog row, or None when this Mac has no copy (e.g. archived elsewhere)."""
    key = row["poster_key"] if row["media_type"] == "video" else row["compressed_key"]
    if not key:
        return None
    p = Path(library) / key
    return p if p.exists() else None


def load(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        return im


def jpeg_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()
