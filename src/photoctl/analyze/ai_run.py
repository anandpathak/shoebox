"""The AI pass: photo vectors (plain-English search) and captions, for items that don't have them yet."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .facedb import now
from .image import load, source_path
from .vectors import open_vectors, save

BATCH = 8


def todo(catalog, vdb, *, search_model: str | None, caption_model: str | None) -> list:
    done: dict[str, set[str]] = {"embed": set(), "caption": set()}
    for pid, step, model in vdb.execute("SELECT photo_id, step, model FROM done"):
        if (step, model) in (("embed", search_model), ("caption", caption_model)):
            done[step].add(pid)
    rows = catalog.execute("SELECT photo_id, media_type, compressed_key, poster_key, caption FROM media "
                           "WHERE deleted = 0 ORDER BY taken_at DESC").fetchall()
    out = []
    for r in rows:
        need_embed = bool(search_model) and r["photo_id"] not in done["embed"]
        need_caption = bool(caption_model) and r["photo_id"] not in done["caption"] and not r["caption"]
        if need_embed or need_caption:
            out.append((r, need_embed, need_caption))
    return out


def _mark(vdb, pid: str, step: str, model: str, error: str | None = None) -> None:
    vdb.execute("INSERT OR REPLACE INTO done (photo_id, step, model, at, error) VALUES (?, ?, ?, ?, ?)",
                (pid, step, model, now(), error))


def analyze_ai(catalog, library: Path, device_id: str, *, embed_images: Callable | None = None,
               search_model: str | None = None, caption: Callable | None = None, caption_model: str | None = None,
               limit: int | None = None, stop: threading.Event | None = None, vdb=None,
               on_item: Callable[[dict], None] | None = None) -> dict:
    """`embed_images(list[Image]) -> ndarray` and `caption(Image) -> str` are the models (None = skip that step)."""
    from ..catalog.ops import Emitter

    vdb = vdb or open_vectors()
    emitter = Emitter(catalog, device_id)
    work = todo(catalog, vdb, search_model=search_model if embed_images else None,
                caption_model=caption_model if caption else None)[:limit]
    counts = {"embedded": 0, "captioned": 0, "skipped": 0, "failed": 0}
    for i in range(0, len(work), BATCH):
        if stop and stop.is_set():
            break
        batch, images = [], []
        for row, need_embed, need_caption in work[i:i + BATCH]:
            path = source_path(library, row)
            if path is None:
                for step, need, model in (("embed", need_embed, search_model), ("caption", need_caption, caption_model)):
                    if need:
                        _mark(vdb, row["photo_id"], step, model, "no local copy")
                counts["skipped"] += 1
                continue
            try:
                images.append(load(path))
                batch.append((row, need_embed, need_caption))
            except Exception as e:  # noqa: BLE001 — unreadable file: note it and move on
                for step, need, model in (("embed", need_embed, search_model), ("caption", need_caption, caption_model)):
                    if need:
                        _mark(vdb, row["photo_id"], step, model, f"{type(e).__name__}: {e}"[:300])
                counts["failed"] += 1
        wanted = [(row, im) for (row, ne, _), im in zip(batch, images) if ne]
        if wanted:
            vecs = embed_images([im for _, im in wanted])
            save(vdb, search_model, [(row["photo_id"], v) for (row, _), v in zip(wanted, vecs)])
            for row, _ in wanted:
                _mark(vdb, row["photo_id"], "embed", search_model)
            counts["embedded"] += len(wanted)
        for (row, _, need_caption), im in zip(batch, images):
            if not need_caption or (stop and stop.is_set()):
                continue
            try:
                text = caption(im)
                if text:
                    emitter.emit("media.caption", {"photo_id": row["photo_id"], "caption": text,
                                                   "model": caption_model.rsplit("/", 1)[-1][:100]})
                _mark(vdb, row["photo_id"], "caption", caption_model)
                counts["captioned"] += 1
                if on_item:
                    on_item({"photo_id": row["photo_id"], "caption": text})
            except Exception as e:  # noqa: BLE001
                _mark(vdb, row["photo_id"], "caption", caption_model, f"{type(e).__name__}: {e}"[:300])
                counts["failed"] += 1
        vdb.commit()
    counts["remaining"] = max(len(work) - (i + BATCH if work else 0), 0) if work else 0
    return counts
