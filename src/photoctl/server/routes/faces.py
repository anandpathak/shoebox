"""Analysis status and naming people from their faces (LOCAL_APP_PLAN.md §17)."""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ...analyze.facedb import clusters, ignore_faces, name_faces, open_faces, rematch, unname_face
from ...analyze.image import load, source_path
from ...catalog.ops import Emitter
from ..deps import db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])


def _fdb():
    conn = open_faces()
    try:
        yield conn
    finally:
        conn.close()


def _emit(request: Request, conn, ops) -> None:
    if ops:
        Emitter(conn, request.app.state.device.device_id).emit_many(ops)


@router.get("/analysis/status")
def status(request: Request, conn: sqlite3.Connection = Depends(db), fdb=Depends(_fdb)):
    from ...analyze import VERSION

    total = conn.execute("SELECT COUNT(*) FROM media WHERE deleted = 0").fetchone()[0]
    done = fdb.execute("SELECT COUNT(*) FROM analyzed WHERE version >= ?", (VERSION,)).fetchone()[0]
    faces = dict(fdb.execute("SELECT status, COUNT(*) FROM faces GROUP BY status").fetchall())
    worker = request.app.state.analysis
    return {"enabled": worker.enabled, "total": total, "analyzed": min(done, total), "faces": faces,
            "unnamed_groups": fdb.execute("SELECT COUNT(DISTINCT cluster) FROM faces WHERE status = 'new'").fetchone()[0],
            **{k: worker.state[k] for k in ("running", "last_error")}}


@router.post("/analysis/run")
def run(request: Request):
    if not request.app.state.analysis.enabled:
        raise HTTPException(409, "Analysis is turned off on this Mac")
    request.app.state.analysis.kick()
    return {"started": True}


@router.get("/faces/groups")
def groups(limit: int = 60, fdb=Depends(_fdb)):
    return clusters(fdb, limit=min(limit, 200))


@router.get("/faces/{face_id}.jpg")
def face_image(face_id: int, request: Request, conn: sqlite3.Connection = Depends(db), fdb=Depends(_fdb)):
    f = fdb.execute("SELECT * FROM faces WHERE face_id = ?", (face_id,)).fetchone()
    row = f and conn.execute("SELECT * FROM media WHERE photo_id = ?", (f["photo_id"],)).fetchone()
    path = row and source_path(request.app.state.settings.library, row)
    if not path:
        raise HTTPException(404, "No picture for that face")
    im = load(path)
    pad = 0.35
    x, y, w, h = f["x"] * im.width, f["y"] * im.height, f["w"] * im.width, f["h"] * im.height
    side = max(w, h) * (1 + 2 * pad)
    cx, cy = x + w / 2, y + h / 2
    box = (int(max(cx - side / 2, 0)), int(max(cy - side / 2, 0)),
           int(min(cx + side / 2, im.width)), int(min(cy + side / 2, im.height)))
    crop = im.crop(box)
    crop.thumbnail((192, 192))
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/faces/groups/{cluster}")
def group_faces(cluster: int, fdb=Depends(_fdb)):
    rows = fdb.execute("SELECT face_id, photo_id FROM faces WHERE cluster = ? AND status = 'new' ORDER BY score DESC",
                       (cluster,)).fetchall()
    return [{"face_id": r["face_id"], "photo_id": r["photo_id"]} for r in rows]


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cluster: int | None = None
    face_ids: list[int] = Field(default_factory=list, max_length=5000)


@router.post("/faces/name")
def name(body: Name, request: Request, conn: sqlite3.Connection = Depends(db), fdb=Depends(_fdb)):
    ids = list(body.face_ids)
    if body.cluster is not None:
        ids += [r[0] for r in fdb.execute("SELECT face_id FROM faces WHERE cluster = ? AND status = 'new'", (body.cluster,))]
    if not ids:
        raise HTTPException(422, "No faces to name")
    person, ops = name_faces(fdb, conn, ids, body.name)
    _emit(request, conn, ops)
    more = rematch(fdb, conn)              # other photos of them are now recognised too
    _emit(request, conn, more)
    return {"person_id": person, "named": len(ids), "also_found": len(more)}


class Faces(BaseModel):
    face_ids: list[int] = Field(default_factory=list, max_length=5000)
    cluster: int | None = None


@router.post("/faces/ignore")
def ignore(body: Faces, fdb=Depends(_fdb)):
    ids = list(body.face_ids)
    if body.cluster is not None:
        ids += [r[0] for r in fdb.execute("SELECT face_id FROM faces WHERE cluster = ? AND status = 'new'", (body.cluster,))]
    if ids:
        ignore_faces(fdb, ids)
    return {"ignored": len(ids)}


@router.post("/faces/{face_id}/not-them")
def not_them(face_id: int, request: Request, conn: sqlite3.Connection = Depends(db), fdb=Depends(_fdb)):
    _emit(request, conn, unname_face(fdb, conn, face_id))
    return {"ok": True}


@router.get("/media/{photo_id}/faces")
def photo_faces(photo_id: str, conn: sqlite3.Connection = Depends(db), fdb=Depends(_fdb)):
    from ...catalog.store import resolve_person

    out = []
    for f in fdb.execute("SELECT * FROM faces WHERE photo_id = ? AND status != 'ignored'", (photo_id,)):
        person = resolve_person(conn, f["person_id"]) if f["person_id"] else None
        name_ = person and conn.execute("SELECT name FROM people WHERE person_id = ?", (person,)).fetchone()
        out.append({"face_id": f["face_id"], "box": [f["x"], f["y"], f["w"], f["h"]], "status": f["status"],
                    "person_id": person, "name": name_[0] if name_ else None, "cluster": f["cluster"]})
    return out
