"""Read the library: search, item details, files, people and devices."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse

from ...catalog.query import media_detail, search, sql
from ...fetch import local_path, presign
from ..deps import bucket, db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
CACHE = {"thumb": "private, max-age=86400, immutable", "compressed": "private, max-age=3600",
         "poster": "private, max-age=86400, immutable"}


@router.get("/search")
def api_search(q: str | None = None, person: str | None = None, place: str | None = None, tag: str | None = None,
               album: str | None = None, date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"), type: str | None = Query(None, pattern="^(photo|video)$"),
               fav: bool | None = None, device: str | None = None, hidden: bool = False, cursor: str | None = None,
               limit: int = Query(60, ge=1, le=500), conn: sqlite3.Connection = Depends(db)):
    try:
        return search(conn, q, person=person, place=place, tag=tag, album=album, date_from=date_from,
                      date_to=date_to, media_type=type, favorite=fav, device=device, include_hidden=hidden,
                      cursor=cursor, limit=limit)
    except (ValueError, sqlite3.OperationalError) as e:
        raise HTTPException(400, f"Couldn't run that search: {e}") from None


@router.get("/search/smart")
def api_smart_search(q: str, person: str | None = None, place: str | None = None, tag: str | None = None,
                     album: str | None = None, date_from: str | None = Query(None, alias="from"),
                     date_to: str | None = Query(None, alias="to"), type: str | None = Query(None, pattern="^(photo|video)$"),
                     fav: bool | None = None, device: str | None = None, hidden: bool = False,
                     limit: int = Query(120, ge=1, le=500), conn: sqlite3.Connection = Depends(db)):
    """Plain-English search ("kids playing in snow"), best matches first. Falls back to keyword search
    when the AI extras aren't installed or no photo has been analysed yet."""
    from ...analyze import ai
    from ...analyze.smart import smart_search
    from ...analyze.vectors import INDEX, open_vectors

    filters = dict(person=person, place=place, tag=tag, album=album, date_from=date_from, date_to=date_to,
                   media_type=type, favorite=fav, device=device, include_hidden=hidden)
    vdb = open_vectors()
    try:
        model = ai.settings()["search_model"]
        index = INDEX.load(vdb, model)
        if not ai.available() or not index.ids:
            return {**search(conn, q, **filters, limit=limit), "mode": "keywords"}
        emb = ai.embedder()
        return smart_search(conn, q, embed_text=emb.text, probability=emb.probability, index=index, limit=limit,
                            **filters)
    except (ValueError, sqlite3.OperationalError) as e:
        raise HTTPException(400, f"Couldn't run that search: {e}") from None
    finally:
        vdb.close()


@router.get("/media/{photo_id}")
def api_media(photo_id: str, conn: sqlite3.Connection = Depends(db)):
    item = media_detail(conn, photo_id)
    if item is None:
        raise HTTPException(404, "No such item")
    return item


def _file(request: Request, conn: sqlite3.Connection, photo_id: str, kind: str, download: bool):
    item = conn.execute(f"SELECT {kind}_key AS key, orig_name FROM media WHERE photo_id = ? AND deleted = 0",
                        (photo_id,)).fetchone()
    if item is None or not item["key"]:
        raise HTTPException(404, f"No {kind} for this item")
    key = item["key"]
    name = Path(key).name
    if download and item["orig_name"]:
        name = f"{Path(item['orig_name']).stem}{Path(key).suffix}"
    local = local_path(request.app.state.settings.library, key)
    if local:
        return FileResponse(local, filename=name if download else None, headers={"Cache-Control": CACHE[kind]},
                            content_disposition_type="attachment" if download else "inline")
    s3 = bucket(request)
    if s3 is None:
        raise HTTPException(404, f"The {kind} isn't on this Mac and the server has no S3 access")
    return RedirectResponse(presign(s3, key, filename=name if download else None), status_code=302)


@router.get("/media/{photo_id}/thumb")
def api_thumb(photo_id: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    return _file(request, conn, photo_id, "thumb", False)


@router.get("/media/{photo_id}/poster")
def api_poster(photo_id: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    return _file(request, conn, photo_id, "poster", False)


@router.get("/media/{photo_id}/compressed")
def api_compressed(photo_id: str, request: Request, download: bool = False, conn: sqlite3.Connection = Depends(db)):
    return _file(request, conn, photo_id, "compressed", download)


@router.get("/people")
def api_people(conn: sqlite3.Connection = Depends(db)):
    return [dict(r) for r in conn.execute(sql("people"))]


@router.get("/devices")
def api_devices(conn: sqlite3.Connection = Depends(db)):
    return [dict(r) for r in conn.execute(sql("devices"))]


@router.get("/stats")
def api_stats(conn: sqlite3.Connection = Depends(db)):
    from ...catalog.store import stats

    return stats(conn)
