"""Turn sidecar records into catalog rows and keep the full-text index in step.

Until the op log lands (M2), archive and backfill call `upsert_record` directly. Import data
(Apple keywords, labels, albums, people) gets deterministic `add_op` ids, so importing the same
sidecar twice is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

MIME = {
    "heic": "image/heic", "heif": "image/heif", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "tif": "image/tiff", "tiff": "image/tiff", "gif": "image/gif", "dng": "image/x-adobe-dng",
    "cr2": "image/x-canon-cr2", "nef": "image/x-nikon-nef", "arw": "image/x-sony-arw",
    "mov": "video/quicktime", "mp4": "video/mp4", "m4v": "video/x-m4v",
}
UNNAMED_PERSON = "_UNKNOWN_"

# Columns refreshed from the sidecar on every upsert. User-editable ones (title, description,
# favorite, hidden) are only set on first insert so later imports never clobber edits.
_FACT_COLUMNS = (
    "sha256", "media_type", "raw_key", "compressed_key", "poster_key", "thumb_key", "meta_key", "orig_name",
    "orig_ext", "mime", "bytes_raw", "bytes_compressed", "width", "height", "duration_s", "taken_at",
    "taken_at_source", "tz", "lat", "lon", "place_country", "place_region", "place_city", "place_locality",
    "place_name", "camera_make", "camera_model",
)


def stamp(device_id: str) -> str:
    """Placeholder clock value until the HLC module (T2.1): wall ms, counter, device."""
    return f"{int(time.time() * 1000):013d}-0000-{device_id}"


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
        "taken_at_source": (record.get("source") or {}).get("type") if record.get("taken_at") else None,
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
                  bytes_compressed: int | None = None, thumb_key: str | None = None) -> str:
    """Insert or refresh one sidecar record (+ its tags, albums, people, source) and its FTS row."""
    hlc = stamp(device.device_id)
    row = media_row(record, bytes_compressed=bytes_compressed, thumb_key=thumb_key)
    pid = row["photo_id"]
    apple = record.get("apple") or {}
    with conn:
        upsert_device(conn, device, hlc)
        cols = list(row) + ["created_hlc", "updated_hlc"]
        conn.execute(
            f"INSERT INTO media ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(photo_id) DO UPDATE SET "
            + ", ".join(f"{c} = COALESCE(excluded.{c}, media.{c})" for c in _FACT_COLUMNS)
            + ", updated_hlc = excluded.updated_hlc",
            [*row.values(), hlc, hlc],
        )
        conn.execute(
            "INSERT OR IGNORE INTO media_sources (photo_id, device_id, source_ref, seen_hlc) VALUES (?, ?, ?, ?)",
            (pid, device.device_id, source_ref(record), hlc),
        )
        for source, values in (("apple-keyword", apple.get("keywords")), ("apple-label", apple.get("labels"))):
            for tag in dict.fromkeys(t.strip() for t in values or [] if t and t.strip()):
                conn.execute("INSERT OR IGNORE INTO media_tags (photo_id, tag, source, add_op) VALUES (?, ?, ?, ?)",
                             (pid, tag.lower(), source, f"import:{pid}:{source}:{tag.lower()}"))
        for album in dict.fromkeys(a for a in apple.get("albums") or [] if a):
            conn.execute("INSERT OR IGNORE INTO media_albums (photo_id, album, add_op) VALUES (?, ?, ?)",
                         (pid, album, f"import:{pid}:album:{album}"))
        for name in dict.fromkeys(n.strip() for n in apple.get("persons") or [] if n and n.strip()):
            if name == UNNAMED_PERSON:
                continue
            per = person_id(name)
            conn.execute("INSERT OR IGNORE INTO people (person_id, name) VALUES (?, ?)", (per, name))
            conn.execute("INSERT OR IGNORE INTO media_people (photo_id, person_id, source, add_op) VALUES (?, ?, ?, ?)",
                         (pid, per, "apple", f"import:{pid}:person:{per}"))
        refresh_fts(conn, pid)
    return pid


def upsert_device(conn: sqlite3.Connection, device: Any, hlc: str) -> None:
    conn.execute(
        "INSERT INTO devices (device_id, name, platform, first_seen_hlc, last_seen_hlc) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(device_id) DO UPDATE SET name = excluded.name, platform = excluded.platform, "
        "last_seen_hlc = excluded.last_seen_hlc",
        (device.device_id, device.name, device.platform, hlc, hlc),
    )


def set_thumb_key(conn: sqlite3.Connection, photo_id: str, thumb_key: str) -> None:
    with conn:
        conn.execute("UPDATE media SET thumb_key = ? WHERE photo_id = ?", (thumb_key, photo_id))


def refresh_fts(conn: sqlite3.Connection, photo_id: str) -> None:
    """Rebuild the FTS row for one item from the current (non-tombstoned) rows."""
    m = conn.execute("SELECT * FROM media WHERE photo_id = ?", (photo_id,)).fetchone()
    conn.execute("DELETE FROM media_fts WHERE photo_id = ?", (photo_id,))
    if m is None or m["deleted"]:
        return
    live = "add_op NOT IN (SELECT add_op FROM tombstones)"
    tags = [r[0] for r in conn.execute(f"SELECT DISTINCT tag FROM media_tags WHERE photo_id = ? AND {live}", (photo_id,))]
    albums = [r[0] for r in conn.execute(f"SELECT DISTINCT album FROM media_albums WHERE photo_id = ? AND {live}", (photo_id,))]
    people = [r[0] for r in conn.execute(
        f"SELECT DISTINCT p.name FROM media_people mp JOIN people p ON p.person_id = COALESCE("
        f"(SELECT merged_into FROM people WHERE person_id = mp.person_id), mp.person_id) "
        f"WHERE mp.photo_id = ? AND mp.{live} AND p.name IS NOT NULL", (photo_id,))]
    place = " ".join(filter(None, (m["place_name"], m["place_locality"], m["place_city"], m["place_region"],
                                   m["place_country"])))
    conn.execute(
        "INSERT INTO media_fts (photo_id, title, description, tags, people, place, orig_name, albums) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_id, m["title"] or "", m["description"] or "", " ".join(tags), " ".join(people), place,
         m["orig_name"] or "", " ".join(albums)),
    )


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
