"""Identity and compressed-derivative helpers."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def photo_id_from_sha256(sha256_hex: str) -> str:
    """First 26 chars of base32(sha256) — content-addressed and filename-safe."""
    return base64.b32encode(bytes.fromhex(sha256_hex)).decode("ascii")[:26]


def compress(src: Path, dest: Path, long_edge: int = 1600, quality: int = 80) -> None:
    """Resize to `long_edge` and re-encode as JPEG using macOS `sips`.

    sips goes through Apple's ImageIO, so it reads HEIC/JPEG/PNG/TIFF/RAW and
    carries the EXIF (incl. GPS, capture date, camera, orientation) and colour
    profile over to the output.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    proc = subprocess.run(
        [
            "sips",
            "-Z", str(long_edge),
            "-s", "format", "jpeg",
            "-s", "formatOptions", str(quality),
            str(src),
            "--out", str(tmp),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sips failed: {proc.stderr.strip() or proc.stdout.strip()}")
    tmp.replace(dest)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found (brew install ffmpeg); original archived without a compressed copy")
    return exe


def _run(cmd: list[str], tmp: Path, dest: Path) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {proc.stderr.strip()[-500:]}")
    tmp.replace(dest)


def compress_video(src: Path, dest: Path, short_edge: int = 720, crf: int = 28) -> None:
    """Transcode to H.264/AAC MP4 with the short edge scaled to `short_edge` (never upscaled).

    `-map_metadata 0` + `use_metadata_tags` keep the QuickTime tags: creation date,
    GPS (ISO6709), camera make/model. ffmpeg applies the rotation flag to the pixels.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.mp4")
    scale = (f"scale='if(gt(iw,ih),-2,min({short_edge},iw))':'if(gt(iw,ih),min({short_edge},ih),-2)'")
    _run([
        _ffmpeg(), "-v", "error", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", scale,
        "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-map_metadata", "0", "-movflags", "+faststart+use_metadata_tags",
        str(tmp),
    ], tmp, dest)


def video_poster(src: Path, dest: Path, long_edge: int = 1600, at_seconds: float = 1.0) -> None:
    """Grab one frame as a JPEG for image pipelines (captioning, faces)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    scale = (f"scale='if(gt(iw,ih),min({long_edge},iw),-2)':'if(gt(iw,ih),-2,min({long_edge},ih))'")
    for seek in (at_seconds, 0):  # clips shorter than 1s: fall back to the first frame
        try:
            _run([_ffmpeg(), "-v", "error", "-y", "-ss", str(seek), "-i", str(src),
                  "-frames:v", "1", "-vf", scale, "-q:v", "3", str(tmp)], tmp, dest)
            return
        except RuntimeError:
            if seek == 0:
                raise
