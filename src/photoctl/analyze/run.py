"""The analysis pass: for each photo not analysed yet, labels → tags and faces → people."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from . import VERSION
from .facedb import add_faces, assign, learn_from_photo_names, now, open_faces, rematch
from .image import load, source_path
from .labels import pick

LIVE = "add_op NOT IN (SELECT add_op FROM tombstones)"


def pending(catalog: sqlite3.Connection, fdb: sqlite3.Connection, *, redo: bool = False) -> list[sqlite3.Row]:
    done = set() if redo else {r[0] for r in fdb.execute("SELECT photo_id FROM analyzed WHERE version >= ?", (VERSION,))}
    rows = catalog.execute("SELECT photo_id, media_type, compressed_key, poster_key FROM media WHERE deleted = 0 "
                           "ORDER BY taken_at DESC").fetchall()
    return [r for r in rows if r["photo_id"] not in done]


def analyze_one(catalog, fdb, library: Path, row, *, classify=None, find_faces=None, read_text=None) -> tuple[list, dict]:
    """Analyse one item. Returns (ops to emit, summary)."""
    from .faces import engine
    from .vision import classify as vision_classify
    from .vision import read_text as vision_text

    classify = classify or vision_classify
    read_text = read_text or vision_text
    find_faces = find_faces or (lambda im: engine().faces(im))
    pid = row["photo_id"]
    path = source_path(library, row)
    if path is None:
        fdb.execute("INSERT OR REPLACE INTO analyzed (photo_id, version, analyzed_at, error) VALUES (?, ?, ?, ?)",
                    (pid, VERSION, now(), "no local copy"))
        fdb.commit()
        return [], {"photo_id": pid, "skipped": "no local copy"}
    im = load(path)
    labels = classify(im)
    fdb.execute("DELETE FROM faces WHERE photo_id = ? AND status = 'new'", (pid,))   # re-analysis
    found = [] if fdb.execute("SELECT 1 FROM faces WHERE photo_id = ? AND status != 'new'", (pid,)).fetchone() \
        else find_faces(im)
    ids = add_faces(fdb, pid, found)
    n_faces = len(found) or fdb.execute("SELECT COUNT(*) FROM faces WHERE photo_id = ? AND status != 'ignored'",
                                        (pid,)).fetchone()[0]
    tags = pick(labels, faces=n_faces, face_area=max((f.area for f in found), default=0.0))
    ops = []
    have = {r[0] for r in catalog.execute(f"SELECT tag FROM media_tags WHERE photo_id = ? AND {LIVE}", (pid,))}
    removed = {r[0] for r in catalog.execute(
        f"SELECT tag FROM media_tags WHERE photo_id = ? AND source = 'vision' AND NOT {LIVE}", (pid,))}
    ops += [("tag.add", {"photo_id": pid, "tag": t, "source": "vision"}) for t in tags if t not in have | removed]
    text = read_text(im)
    current = catalog.execute("SELECT text_in_image FROM media WHERE photo_id = ?", (pid,)).fetchone()[0]
    if text and text != current:
        ops.append(("media.text", {"photo_id": pid, "text": text}))
    learnt = learn_from_photo_names(fdb, catalog, pid)
    ops += assign(fdb, catalog, ids)
    fdb.execute("INSERT OR REPLACE INTO analyzed (photo_id, version, analyzed_at, faces, labels, error) "
                "VALUES (?, ?, ?, ?, ?, NULL)", (pid, VERSION, now(), len(found), json.dumps(labels[:20])))
    fdb.commit()
    return ops, {"photo_id": pid, "tags": tags, "faces": len(found), "learnt": learnt, "text": bool(text)}


def analyze_pending(catalog, library: Path, device_id: str, *, limit: int | None = None, redo: bool = False,
                    on_item: Callable[[dict], None] | None = None, stop: threading.Event | None = None,
                    fdb=None, **engines) -> dict:
    from ..catalog.ops import Emitter

    fdb = fdb or open_faces()
    emitter = Emitter(catalog, device_id)
    todo = pending(catalog, fdb, redo=redo)[:limit]
    counts = {"analyzed": 0, "skipped": 0, "failed": 0, "tags": 0, "faces": 0}
    learnt = False
    for row in todo:
        if stop and stop.is_set():
            break
        try:
            ops, info = analyze_one(catalog, fdb, library, row, **engines)
            if ops:
                emitter.emit_many(ops)
            counts["skipped" if "skipped" in info else "analyzed"] += 1
            counts["tags"] += len(info.get("tags", []))
            counts["faces"] += info.get("faces", 0)
            learnt |= info.get("learnt", False)
        except Exception as e:     # one bad file mustn't stop the rest
            fdb.execute("INSERT OR REPLACE INTO analyzed (photo_id, version, analyzed_at, error) VALUES (?, ?, ?, ?)",
                        (row["photo_id"], VERSION, now(), f"{type(e).__name__}: {e}"[:300]))
            fdb.commit()
            counts["failed"] += 1
            info = {"photo_id": row["photo_id"], "error": str(e)}
        if on_item:
            on_item(info)
    if learnt:     # a name learnt from Apple Photos can identify faces seen earlier in this pass
        ops = rematch(fdb, catalog)
        if ops:
            emitter.emit_many(ops)
    counts["remaining"] = max(len(todo) - sum(counts[k] for k in ("analyzed", "skipped", "failed")), 0)
    return counts
