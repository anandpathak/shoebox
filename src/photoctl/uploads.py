"""Files uploaded from browsers and phones, as archive items (LOCAL_APP_PLAN.md §9.3).

Metadata comes from the file itself (EXIF for photos, ffprobe for videos). Browsers often strip GPS
(and iOS can convert HEIC to JPEG), so items record `metadata_fidelity: "browser"` and are flagged
`metadata_incomplete` when the capture date or location is missing.
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".gif", ".webp", ".dng", ".cr2", ".nef", ".arw"}


def _rational(v) -> float:
    try:
        return float(v)
    except TypeError:
        return v[0] / v[1]


def _gps(gps: dict) -> tuple[float | None, float | None]:
    try:
        lat = sum(_rational(x) / 60 ** i for i, x in enumerate(gps[2])) * (-1 if gps.get(1) == "S" else 1)
        lon = sum(_rational(x) / 60 ** i for i, x in enumerate(gps[4])) * (-1 if gps.get(3) == "W" else 1)
        return round(lat, 6), round(lon, 6)
    except (KeyError, TypeError, ZeroDivisionError, IndexError):
        return None, None


def _exif_date(value: str | None, offset: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if offset and re.match(r"^[+-]\d{2}:\d{2}$", offset):
        sign = 1 if offset[0] == "+" else -1
        from datetime import timedelta

        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))))
    return dt


def image_metadata(path: Path) -> dict:
    from PIL import Image

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass
    out = {"width": None, "height": None, "taken_at": None, "lat": None, "lon": None, "make": None, "model": None}
    try:
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            exif = im.getexif()
            ifd = exif.get_ifd(0x8769)
            out["taken_at"] = _exif_date(ifd.get(36867) or exif.get(306), ifd.get(36881))
            out["lat"], out["lon"] = _gps(dict(exif.get_ifd(0x8825)))
            out["make"], out["model"] = exif.get(271), exif.get(272)
    except Exception:
        pass
    return out


def video_metadata(path: Path) -> dict:
    out = {"width": None, "height": None, "taken_at": None, "lat": None, "lon": None, "make": None, "model": None,
           "duration": None}
    exe = shutil.which("ffprobe")
    if not exe:
        return out
    proc = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return out
    info = json.loads(proc.stdout or "{}")
    fmt = info.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    out["width"], out["height"] = video.get("width"), video.get("height")
    out["duration"] = float(fmt["duration"]) if fmt.get("duration") else None
    when = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if when:
        try:
            out["taken_at"] = datetime.fromisoformat(when.replace("Z", "+00:00").replace("+0000", "+00:00"))
        except ValueError:
            pass
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    m = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc or "")
    if m:
        out["lat"], out["lon"] = float(m.group(1)), float(m.group(2))
    out["make"], out["model"] = tags.get("com.apple.quicktime.make"), tags.get("com.apple.quicktime.model")
    return out


@dataclass
class FileItem:
    """A file in the inbox, presented the way the archive pipeline expects an item."""

    path: str
    original_filename: str
    device_id: str
    upload_id: str
    last_modified: datetime | None = None
    source_type: str = "upload"

    def __post_init__(self):
        p = Path(self.path)
        self.ismovie = p.suffix.lower() in VIDEO_EXT
        meta = video_metadata(p) if self.ismovie else image_metadata(p)
        self._meta = meta
        self.width, self.height = meta["width"], meta["height"]
        self.date = meta["taken_at"] or self.last_modified
        self.date_source = "file" if meta["taken_at"] else "last_modified" if self.last_modified else None
        self.uti_original = mimetypes.guess_type(self.original_filename)[0]
        self.has_raw, self.path_raw, self.live_photo, self.path_live_photo = False, None, False, None

    @property
    def ref(self) -> str:
        return f"{self.device_id}:{self.upload_id}:{self.original_filename}"

    @property
    def source_ref(self) -> str:
        return f"upload:{self.ref}"

    @property
    def source_device_id(self) -> str:
        return self.device_id

    @property
    def metadata_incomplete(self) -> bool:
        return self._meta["taken_at"] is None or self._meta["lat"] is None

    @property
    def extra_metadata(self) -> dict:
        missing = [k for k, v in (("capture date", self._meta["taken_at"]), ("location", self._meta["lat"])) if v is None]
        return {"metadata_fidelity": "browser", "metadata_incomplete": bool(missing), "metadata_missing": missing,
                "uploaded_by": self.device_id, "taken_at_source": self.date_source}

    def json(self) -> str:
        m = self._meta
        return json.dumps({
            "title": None, "description": None, "favorite": False, "hidden": False, "keywords": [], "labels": [],
            "albums": [], "persons": [], "latitude": m["lat"], "longitude": m["lon"], "place": {},
            "exif_info": {"camera_make": m["make"], "camera_model": m["model"], "duration": m.get("duration")},
        })
