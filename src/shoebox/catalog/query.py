"""Search the catalog with the shared SQL in shoebox/queries (also used by the TypeScript app).

`parse_query` and `build_fts` must behave exactly like their TypeScript twins; both are checked
against spec/query_vectors.json.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from functools import lru_cache
from importlib import resources
from pathlib import Path

MONTHS = {name: i for i, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
    ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
    ("december", "dec")], start=1) for name in names}
TYPES = {"photo": "photo", "photos": "photo", "video": "video", "videos": "video"}
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
YEAR = re.compile(r"^(19|20)\d\d$")
PAGE = 60


@lru_cache(maxsize=None)
def sql(name: str) -> str:
    return resources.files("shoebox").joinpath("queries", f"{name}.sql").read_text()


def tokens(text: str | None) -> list[str]:
    return TOKEN.findall((text or "").lower())


def parse_query(text: str | None) -> dict:
    """Free text → words for full-text search plus date/type filters ("beach december 2024 videos")."""
    out = {"terms": [], "date_from": None, "date_to": None, "month": None, "media_type": None}
    year = month = None
    for tok in tokens(text):
        if YEAR.match(tok) and year is None:
            year = int(tok)
        elif tok in MONTHS and month is None:
            month = MONTHS[tok]
        elif tok in TYPES and out["media_type"] is None:
            out["media_type"] = TYPES[tok]
        else:
            out["terms"].append(tok)
    if year and month:
        out["date_from"] = f"{year:04d}-{month:02d}-01"
        out["date_to"] = f"{year + (month == 12):04d}-{month % 12 + 1:02d}-01"
    elif year:
        out["date_from"], out["date_to"] = f"{year:04d}-01-01", f"{year + 1:04d}-01-01"
    elif month:
        out["month"] = f"{month:02d}"
    return out


def _phrase(tok: str, prefix: bool) -> str:
    return '"' + tok.replace('"', '""') + '"' + ("*" if prefix else "")


def build_fts(terms: list[str] | None = None, *, person: str | None = None, place: str | None = None,
              tag: str | None = None, album: str | None = None) -> str | None:
    """A safe FTS5 expression: every word quoted (user text never becomes FTS syntax), ANDed together.
    Free-text words match as prefixes anywhere; person/place/tag/album words match in that column."""
    parts = [_phrase(t, True) for t in terms or []]
    for column, value, prefix in (("people", person, False), ("place", place, True), ("tags", tag, False),
                                  ("albums", album, False)):
        parts += [f"{column} : {_phrase(t, prefix)}" for t in tokens(value)]
    return " AND ".join(parts) or None


def encode_cursor(row) -> str:
    return base64.urlsafe_b64encode(json.dumps([row["taken_at"] or "", row["photo_id"]]).encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    taken, pid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    return taken, pid


def search(conn: sqlite3.Connection, text: str | None = None, *, person: str | None = None, place: str | None = None,
           tag: str | None = None, album: str | None = None, date_from: str | None = None, date_to: str | None = None,
           media_type: str | None = None, favorite: bool | None = None, device: str | None = None,
           include_hidden: bool = False, cursor: str | None = None, limit: int = PAGE) -> dict:
    """Returns {"items": [...], "next": cursor-or-None, "query": parsed}. Explicit filters beat parsed ones."""
    parsed = parse_query(text)
    taken, pid = decode_cursor(cursor)
    params = {
        "fts": build_fts(parsed["terms"], person=person, place=place, tag=tag, album=album),
        "date_from": date_from or parsed["date_from"], "date_to": date_to or parsed["date_to"],
        "month": None if (date_from or date_to) else parsed["month"],
        "media_type": media_type or parsed["media_type"],
        "favorite": None if favorite is None else int(favorite), "device": device,
        "include_hidden": int(include_hidden), "cursor_taken": taken, "cursor_id": pid, "limit": limit + 1,
    }
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql("search"), params)]
    more = len(rows) > limit
    rows = rows[:limit]
    for r in rows:
        r["devices"] = sorted((r.get("devices") or "").split(",")) if r.get("devices") else []
    return {"items": rows, "next": encode_cursor(rows[-1]) if more and rows else None, "query": parsed}


def search_catalogs(paths: list[Path], text: str | None = None, *, limit: int = PAGE, **filters) -> dict:
    """Search several catalog files (e.g. downloaded iPhone + Android catalogs) and merge the results.
    A photo in more than one catalog appears once, with the devices from all of them."""
    merged: dict[str, dict] = {}
    for p in paths:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            for item in search(conn, text, limit=limit, **filters)["items"]:
                if item["photo_id"] in merged:
                    merged[item["photo_id"]]["devices"] = sorted(set(merged[item["photo_id"]]["devices"]) |
                                                                 set(item["devices"]))
                    merged[item["photo_id"]]["catalogs"].append(str(p))
                else:
                    merged[item["photo_id"]] = {**item, "catalogs": [str(p)]}
        finally:
            conn.close()
    items = sorted(merged.values(), key=lambda r: (r["taken_at"] or "", r["photo_id"]), reverse=True)
    return {"items": items[:limit]}


def media_detail(conn: sqlite3.Connection, photo_id: str) -> dict | None:
    from .store import resolve_person

    conn.row_factory = sqlite3.Row
    row = conn.execute(sql("media_detail"), {"photo_id": photo_id}).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("tags", "albums", "sources", "people"):
        d[k] = json.loads(d.pop(f"{k}_json") or "[]")
    people = {}
    for p in d["people"]:
        survivor = resolve_person(conn, p["person_id"])
        name = conn.execute("SELECT name FROM people WHERE person_id = ?", (survivor,)).fetchone()
        people[survivor] = {"person_id": survivor, "name": name[0] if name else None,
                            "add_ops": people.get(survivor, {}).get("add_ops", []) + [p["add_op"]]}
    d["people"] = sorted(people.values(), key=lambda p: p["name"] or "")
    d.pop("field_hlc", None)
    return d
