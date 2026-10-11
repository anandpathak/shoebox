"""Apply ops to the catalog — idempotent and order-independent (LOCAL_APP_PLAN.md §5).

Rules:
* Every scalar field is last-writer-wins by HLC. `field_hlc` stores "<priority>|<hlc>" per field:
  priority 1 for real edits, 0 for values that came from an import (Apple title/favorite), so an
  import never overrides an edit, whatever order they arrive in. Null import values don't take part.
* Tags, albums and people-on-photo are observed-remove sets: a remove tombstones only the add ops it
  had seen, so a concurrent add survives.
* An op is applied at most once (`ops.op_id` primary key).
* Ops that edit a photo whose upsert hasn't arrived yet wait in `deferred_ops`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from .ops import store_op, validate
from .store import _FACT_COLUMNS, UNNAMED_PERSON, media_row, person_id, refresh_fts

EDITABLE = ("title", "description", "favorite", "hidden")
IMPORT, EDIT = "0", "1"


def _ver(priority: str, hlc: str) -> str:
    return f"{priority}|{hlc}"


def _load(raw: str | None) -> dict:
    return json.loads(raw) if raw else {}


def _dump(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _norm(label: str) -> str:
    return " ".join(label.split())


class _Ctx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.touched: set[str] = set()      # photo_ids needing an FTS refresh
        self.people: set[str] = set()       # person_ids whose photos need an FTS refresh


# ---------------------------------------------------------------- LWW helpers

def _lww_row(ctx: _Ctx, table: str, key_col: str, key: str, values: dict, version: str,
             defaults: dict | None = None) -> None:
    """Set each field in `values` if `version` beats that field's stored version. Creates the row if needed."""
    conn = ctx.conn
    row = conn.execute(f"SELECT field_hlc FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    if row is None:
        conn.execute(f"INSERT INTO {table} ({key_col}{''.join(', ' + c for c in (defaults or {}))}) "
                     f"VALUES (?{', ?' * len(defaults or {})})", (key, *(defaults or {}).values()))
        fh = {}
    else:
        fh = _load(row[0])
    changed = {f: v for f, v in values.items() if version > fh.get(f, "")}
    if not changed:
        return
    for f in changed:
        fh[f] = version
    sets = ", ".join(f"{f} = ?" for f in changed)
    conn.execute(f"UPDATE {table} SET {sets}, field_hlc = ? WHERE {key_col} = ?",
                 (*changed.values(), _dump(fh), key))


def _media_exists(ctx: _Ctx, photo_id: str) -> bool:
    return ctx.conn.execute("SELECT 1 FROM media WHERE photo_id = ?", (photo_id,)).fetchone() is not None


def _defer(ctx: _Ctx, op: dict, photo_id: str) -> None:
    ctx.conn.execute("INSERT OR IGNORE INTO deferred_ops (op_id, photo_id) VALUES (?, ?)", (op["op_id"], photo_id))


# ---------------------------------------------------------------- handlers

def _media_upsert(ctx: _Ctx, op: dict) -> None:
    body, hlc = op["body"], op["hlc"]
    record = body["record"]
    row = media_row(record, bytes_compressed=body.get("bytes_compressed"))
    pid = row["photo_id"]
    conn = ctx.conn
    existing = conn.execute("SELECT created_hlc, updated_hlc FROM media WHERE photo_id = ?", (pid,)).fetchone()
    if existing is None:
        conn.execute("INSERT INTO media (photo_id, sha256, media_type, raw_key, meta_key, created_hlc, updated_hlc) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (pid, row["sha256"], row["media_type"], row["raw_key"], row["meta_key"], hlc, hlc))
    else:
        conn.execute("UPDATE media SET created_hlc = MIN(created_hlc, ?), updated_hlc = MAX(updated_hlc, ?) "
                     "WHERE photo_id = ?", (hlc, hlc, pid))
    facts = {c: row[c] for c in _FACT_COLUMNS if row.get(c) is not None}
    _lww_row(ctx, "media", "photo_id", pid, facts, _ver(EDIT, hlc))
    imported = {f: row[f] for f in EDITABLE if row.get(f) is not None}
    _lww_row(ctx, "media", "photo_id", pid, imported, _ver(IMPORT, hlc))

    apple = record.get("apple") or {}
    for source, values in (("apple-keyword", apple.get("keywords")), ("apple-label", apple.get("labels"))):
        for tag in dict.fromkeys(_norm(t).lower() for t in values or [] if t and _norm(t)):
            conn.execute("INSERT OR IGNORE INTO media_tags (photo_id, tag, source, add_op) VALUES (?, ?, ?, ?)",
                         (pid, tag, source, f"import:{pid}:{source}:{tag}"))
    for album in dict.fromkeys(_norm(a) for a in apple.get("albums") or [] if a and _norm(a)):
        conn.execute("INSERT OR IGNORE INTO media_albums (photo_id, album, add_op) VALUES (?, ?, ?)",
                     (pid, album, f"import:{pid}:album:{album}"))
    for name in dict.fromkeys(_norm(n) for n in apple.get("persons") or [] if n and _norm(n)):
        if name == UNNAMED_PERSON:
            continue
        per = person_id(name)
        _lww_row(ctx, "people", "person_id", per, {"name": name}, _ver(IMPORT, hlc))
        conn.execute("INSERT OR IGNORE INTO media_people (photo_id, person_id, source, add_op) VALUES (?, ?, ?, ?)",
                     (pid, per, "apple", f"import:{pid}:person:{per}"))
    ctx.touched.add(pid)
    _replay_deferred(ctx, pid)


def _replay_deferred(ctx: _Ctx, photo_id: str) -> None:
    rows = ctx.conn.execute(
        "SELECT o.* FROM deferred_ops d JOIN ops o ON o.op_id = d.op_id WHERE d.photo_id = ? ORDER BY o.hlc",
        (photo_id,)).fetchall()
    ctx.conn.execute("DELETE FROM deferred_ops WHERE photo_id = ?", (photo_id,))
    for r in rows:
        _dispatch(ctx, {"op_id": r["op_id"], "hlc": r["hlc"], "device_id": r["device_id"], "kind": r["kind"],
                        "entity": r["entity"], "body": json.loads(r["body"])})


def _media_source(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    ctx.conn.execute(
        "INSERT INTO media_sources (photo_id, device_id, source_ref, seen_hlc) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(photo_id, device_id, source_ref) DO UPDATE SET seen_hlc = MIN(seen_hlc, excluded.seen_hlc)",
        (b["photo_id"], b["device_id"], b["source_ref"], op["hlc"]))


def _media_set(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    if not _media_exists(ctx, b["photo_id"]):
        return _defer(ctx, op, b["photo_id"])
    value = b["value"]
    if b["field"] in ("favorite", "hidden"):
        value = int(bool(value))
    _lww_row(ctx, "media", "photo_id", b["photo_id"], {b["field"]: value}, _ver(EDIT, op["hlc"]))
    ctx.touched.add(b["photo_id"])


def _media_deleted(ctx: _Ctx, op: dict) -> None:
    pid = op["body"]["photo_id"]
    if not _media_exists(ctx, pid):
        return _defer(ctx, op, pid)
    _lww_row(ctx, "media", "photo_id", pid, {"deleted": int(op["kind"] == "media.delete")}, _ver(EDIT, op["hlc"]))
    ctx.touched.add(pid)


def _set_add(table: str, col: str, lower: bool):
    def handler(ctx: _Ctx, op: dict) -> None:
        b = op["body"]
        value = _norm(b[col]).lower() if lower else _norm(b[col])
        extra = ", source" if table == "media_tags" else ""
        ctx.conn.execute(f"INSERT OR IGNORE INTO {table} (photo_id, {col}, add_op{extra}) "
                         f"VALUES (?, ?, ?{', ?' if extra else ''})",
                         (b["photo_id"], value, op["op_id"], *(["user"] if extra else [])))
        ctx.touched.add(b["photo_id"])
    return handler


def _set_remove(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    # Several removes can cancel the same add; keep the earliest so the result is order-independent.
    ctx.conn.executemany("INSERT INTO tombstones (add_op, removed_by) VALUES (?, ?) ON CONFLICT(add_op) "
                         "DO UPDATE SET removed_by = MIN(removed_by, excluded.removed_by)",
                         [(a, op["op_id"]) for a in b["observed_add_ops"]])
    ctx.touched.add(b["photo_id"])
    if "person_id" in b:
        ctx.people.add(b["person_id"])


def _media_person_add(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    ctx.conn.execute("INSERT OR IGNORE INTO media_people (photo_id, person_id, source, add_op) VALUES (?, ?, ?, ?)",
                     (b["photo_id"], b["person_id"], "user", op["op_id"]))
    ctx.touched.add(b["photo_id"])


def _person_name(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    name = b["name"] if op["kind"] == "person.create" else b["value"]
    _lww_row(ctx, "people", "person_id", b["person_id"], {"name": _norm(name)}, _ver(EDIT, op["hlc"]))
    ctx.people.add(b["person_id"])


def _person_merge(ctx: _Ctx, op: dict) -> None:
    b = op["body"]
    if b["from"] == b["into"]:
        return
    _lww_row(ctx, "people", "person_id", b["from"], {"merged_into": b["into"]}, _ver(EDIT, op["hlc"]))
    ctx.people.update((b["from"], b["into"]))


def _device(ctx: _Ctx, op: dict) -> None:
    b, hlc = op["body"], op["hlc"]
    values = ({"name": b["name"], "platform": b["platform"]} if op["kind"] == "device.register"
              else {b["field"]: b["value"]})
    _lww_row(ctx, "devices", "device_id", b["device_id"], values, _ver(EDIT, hlc))
    ctx.conn.execute("UPDATE devices SET first_seen_hlc = MIN(COALESCE(first_seen_hlc, ?), ?), "
                     "last_seen_hlc = MAX(COALESCE(last_seen_hlc, ?), ?) WHERE device_id = ?",
                     (hlc, hlc, hlc, hlc, b["device_id"]))


HANDLERS = {
    "media.upsert": _media_upsert,
    "media.source": _media_source,
    "media.set": _media_set,
    "media.delete": _media_deleted,
    "media.restore": _media_deleted,
    "tag.add": _set_add("media_tags", "tag", lower=True),
    "tag.remove": _set_remove,
    "album.add": _set_add("media_albums", "album", lower=False),
    "album.remove": _set_remove,
    "media.person.add": _media_person_add,
    "media.person.remove": _set_remove,
    "person.create": _person_name,
    "person.set": _person_name,
    "person.merge": _person_merge,
    "device.register": _device,
    "device.set": _device,
}


def _dispatch(ctx: _Ctx, op: dict) -> None:
    HANDLERS[op["kind"]](ctx, op)


def _refresh(ctx: _Ctx) -> None:
    if ctx.people:
        # photos of these people and of everyone merged into them, transitively
        ids = set(ctx.people)
        while True:
            more = {r[0] for r in ctx.conn.execute(
                f"SELECT person_id FROM people WHERE merged_into IN ({','.join('?' * len(ids))})", list(ids))} - ids
            if not more:
                break
            ids |= more
        ctx.touched |= {r[0] for r in ctx.conn.execute(
            f"SELECT DISTINCT photo_id FROM media_people WHERE person_id IN ({','.join('?' * len(ids))})", list(ids))}
    for pid in sorted(ctx.touched):
        refresh_fts(ctx.conn, pid)


def apply_ops(conn: sqlite3.Connection, ops: Iterable[dict], *, in_transaction: bool = False,
              trusted: bool = False) -> int:
    """Apply ops (any order, any duplicates). Returns how many were new. Validates unless `trusted`."""
    ordered = sorted(ops, key=lambda o: o["hlc"])
    if not trusted:
        for op in ordered:
            validate(op)
    ctx = _Ctx(conn)

    def run() -> int:
        n = 0
        for op in ordered:
            if not store_op(conn, op):
                continue
            _dispatch(ctx, op)
            n += 1
        _refresh(ctx)
        return n

    if in_transaction:
        return run()
    with conn:
        return run()
