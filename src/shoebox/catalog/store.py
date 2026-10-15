"""Turn sidecar records into catalog rows and keep the full-text index in step.

Every write goes through ops (`upsert_record` emits them; `apply.py` applies them). Import data
(Apple keywords, labels, albums, people) gets deterministic `add_op` ids, so importing the same
sidecar twice changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

MIME = {
    "heic": "image/heic", "heif": "image/heif", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "tif": "image/tiff", "tiff": "image/tiff", "gif": "image/gif", "dng": "image/x-adobe-dng",
    "cr2": "image/x-canon-cr2", "nef": "image/x-nikon-nef", "arw": "image/x-sony-arw",
    "mov": "video/quicktime", "mp4": "video/mp4", "m4v": "video/x-m4v",
}
UNNAMED_PERSON = "_UNKNOWN_"

# Facts from the sidecar; each is last-writer-wins on its own. The user-editable fields (title,
# description, favorite, hidden) are imported at a lower priority so imports never beat edits.
_FACT_COLUMNS = (
    "sha256", "media_type", "raw_key", "compressed_key", "poster_key", "thumb_key", "meta_key", "orig_name",
    "orig_ext", "mime", "bytes_raw", "bytes_compressed", "width", "height", "duration_s", "taken_at",
    "taken_at_source", "tz", "lat", "lon", "place_country", "place_region", "place_city", "place_locality",
    "place_name", "camera_make", "camera_model",
)


def person_id(name: str) -> str:
    return "p_" + hashlib.sha1(name.strip().lower().encode()).hexdigest()[:16]


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return next((v for v in value if v), None)
    return value or None


def _place(apple: dict) -> dict:
    place = apple.get("place") or {}
    addr = place.get("address") or {}
    names = place.get("names") or {}
    return {
        "place_country": addr.get("country") or _first(names.get("country")),
        "place_region": addr.get("state_province") or _first(names.get("state_province")),
        "place_city": addr.get("city") or _first(names.get("city")),
        "place_locality": addr.get("sub_locality") or _first(names.get("additional_city_info")),
        "place_name": place.get("name") or _first(names.get("area_of_interest")),
    }


def media_row(record: dict, *, bytes_compressed: int | None = None, thumb_key: str | None = None) -> dict:
    apple = record.get("apple") or {}
    exif = apple.get("exif_info") or {}
    file = record.get("file") or {}
    keys = record.get("keys") or {}
    ext = (file.get("orig_ext") or "").lower()
    lat = apple.get("latitude") if apple.get("latitude") is not None else exif.get("latitude")
    lon = apple.get("longitude") if apple.get("longitude") is not None else exif.get("longitude")
    row = {
        "photo_id": record["photo_id"],
        "sha256": file["sha256"],
        "media_type": record.get("media_type") or ("video" if apple.get("ismovie") else "photo"),
        "raw_key": keys["raw"],
        "compressed_key": keys.get("compressed"),
        "poster_key": keys.get("poster"),
        "thumb_key": thumb_key or keys.get("thumb"),
        "meta_key": keys["meta"],
        "orig_name": file.get("orig_name"),
        "orig_ext": ext or None,
        "mime": MIME.get(ext),
        "bytes_raw": file.get("bytes_raw"),
        "bytes_compressed": bytes_compressed,
        "width": file.get("width"),
        "height": file.get("height"),
        "duration_s": exif.get("duration"),
        "taken_at": record.get("taken_at"),
        "taken_at_source": (record.get("taken_at_source") or (record.get("source") or {}).get("type")
                            if record.get("taken_at") else None),
        "tz": exif.get("tzname"),
        "lat": lat,
        "lon": lon,
        "camera_make": exif.get("camera_make"),
        "camera_model": exif.get("camera_model"),
        "title": apple.get("title"),
        "description": apple.get("description"),
        "favorite": int(bool(apple.get("favorite"))),
        "hidden": int(bool(apple.get("hidden"))),
    }
    row.update(_place(apple))
    return row


def source_ref(record: dict) -> str:
    src = record.get("source") or {}
    return f"{src.get('type', 'unknown')}:{src.get('ref', '')}"


def upsert_record(conn: sqlite3.Connection, record: dict, device: Any, *,
                  bytes_compressed: int | None = None, source_device_id: str | None = None) -> str:
    """Record one archived item as ops (device.register if new, media.upsert, media.source) and apply them.
    `source_device_id` credits the item to another device (e.g. the phone that uploaded it)."""
    from .ops import Emitter

    items: list[tuple[str, dict]] = []
    known = conn.execute("SELECT name, platform FROM devices WHERE device_id = ?", (device.device_id,)).fetchone()
    if known is None or tuple(known) != (device.name, device.platform):
        items.append(("device.register", {"device_id": device.device_id, "name": device.name,
                                          "platform": device.platform}))
    body = {"record": record}
    if bytes_compressed is not None:
        body["bytes_compressed"] = bytes_compressed
    items.append(("media.upsert", body))
    items.append(("media.source", {"photo_id": record["photo_id"], "device_id": source_device_id or device.device_id,
                                   "source_ref": source_ref(record)}))
    Emitter(conn, device.device_id).emit_many(items)
    return record["photo_id"]


def refresh_fts(conn: sqlite3.Connection, photo_id: str) -> None:
    """Rebuild the FTS row for one item from the current (non-tombstoned) rows."""
    m = conn.execute("SELECT * FROM media WHERE photo_id = ?", (photo_id,)).fetchone()
    conn.execute("DELETE FROM media_fts WHERE photo_id = ?", (photo_id,))
    if m is None or m["deleted"]:
        return
    live = "add_op NOT IN (SELECT add_op FROM tombstones)"
    tags = [r[0] for r in conn.execute(f"SELECT DISTINCT tag FROM media_tags WHERE photo_id = ? AND {live}", (photo_id,))]
    albums = [r[0] for r in conn.execute(f"SELECT DISTINCT album FROM media_albums WHERE photo_id = ? AND {live}", (photo_id,))]
    survivors = {resolve_person(conn, r[0]) for r in conn.execute(
        f"SELECT DISTINCT person_id FROM media_people WHERE photo_id = ? AND {live}", (photo_id,))}
    people = sorted(filter(None, (
        (conn.execute("SELECT name FROM people WHERE person_id = ?", (p,)).fetchone() or [None])[0]
        for p in survivors)))
    place = " ".join(filter(None, (m["place_name"], m["place_locality"], m["place_city"], m["place_region"],
                                   m["place_country"])))
    conn.execute(
        "INSERT INTO media_fts (photo_id, title, description, tags, people, place, orig_name, albums) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_id, m["title"] or "", " ".join(filter(None, (m["description"], m["caption"], m["text_in_image"]))), " ".join(tags),
         " ".join(people), place,
         m["orig_name"] or "", " ".join(albums)),
    )


def resolve_person(conn: sqlite3.Connection, person: str, limit: int = 20) -> str:
    """Follow merged_into links to the surviving person (a cycle resolves to its smallest id)."""
    seen: list[str] = []
    while person not in seen and len(seen) < limit:
        seen.append(person)
        row = conn.execute("SELECT merged_into FROM people WHERE person_id = ?", (person,)).fetchone()
        if not row or not row[0]:
            return person
        person = row[0]
    return min(seen)


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "photos": q("SELECT COUNT(*) FROM media WHERE media_type = 'photo' AND deleted = 0"),
        "videos": q("SELECT COUNT(*) FROM media WHERE media_type = 'video' AND deleted = 0"),
        "people": q("SELECT COUNT(*) FROM people WHERE merged_into IS NULL"),
        "tags": q("SELECT COUNT(DISTINCT tag) FROM media_tags"),
        "devices": q("SELECT COUNT(*) FROM devices"),
        "bytes_raw": q("SELECT COALESCE(SUM(bytes_raw), 0) FROM media WHERE deleted = 0"),
    }


def load_record(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
