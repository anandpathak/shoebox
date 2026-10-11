"""Change the library. Every write is an op (LOCAL_APP_PLAN.md §4) — nothing touches tables directly."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...catalog.ops import Emitter, InvalidOp
from ...catalog.query import media_detail
from ...catalog.store import person_id, resolve_person
from ..deps import db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
LIVE = "add_op NOT IN (SELECT add_op FROM tombstones)"


class MediaPatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    favorite: bool | None = None
    hidden: bool | None = None


class Label(BaseModel):
    value: str = Field(min_length=1, max_length=200)


class PersonAdd(BaseModel):
    person_id: str | None = None
    name: str | None = Field(None, min_length=1, max_length=200)


class PersonPatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class Merge(BaseModel):
    into: str


def _emitter(request: Request, conn: sqlite3.Connection) -> Emitter:
    return Emitter(conn, request.app.state.device.device_id)


def _exists(conn, photo_id: str) -> None:
    if conn.execute("SELECT 1 FROM media WHERE photo_id = ?", (photo_id,)).fetchone() is None:
        raise HTTPException(404, "No such item")


def _emit(request, conn, items):
    try:
        return _emitter(request, conn).emit_many(items)
    except InvalidOp as e:
        raise HTTPException(422, str(e)) from None


@router.patch("/media/{photo_id}")
def patch_media(photo_id: str, body: MediaPatch, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to change")
    _emit(request, conn, [("media.set", {"photo_id": photo_id, "field": f, "value": v}) for f, v in changes.items()])
    return media_detail(conn, photo_id)


@router.post("/media/{photo_id}/delete")
def delete_media(photo_id: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    """Remove from the library (a tombstone). The original stays in S3; restore brings it back."""
    _exists(conn, photo_id)
    _emit(request, conn, [("media.delete", {"photo_id": photo_id})])
    return {"photo_id": photo_id, "deleted": True}


@router.post("/media/{photo_id}/restore")
def restore_media(photo_id: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    _emit(request, conn, [("media.restore", {"photo_id": photo_id})])
    return media_detail(conn, photo_id)


def _live_adds(conn, table: str, col: str, photo_id: str, value: str) -> list[str]:
    return [r[0] for r in conn.execute(f"SELECT add_op FROM {table} WHERE photo_id = ? AND {col} = ? AND {LIVE}",
                                       (photo_id, value))]


@router.post("/media/{photo_id}/tags")
def add_tag(photo_id: str, body: Label, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    _emit(request, conn, [("tag.add", {"photo_id": photo_id, "tag": body.value})])
    return media_detail(conn, photo_id)


@router.delete("/media/{photo_id}/tags/{tag}")
def remove_tag(photo_id: str, tag: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    seen = _live_adds(conn, "media_tags", "tag", photo_id, " ".join(tag.split()).lower())
    if seen:
        _emit(request, conn, [("tag.remove", {"photo_id": photo_id, "tag": tag, "observed_add_ops": seen})])
    return media_detail(conn, photo_id)


@router.post("/media/{photo_id}/albums")
def add_album(photo_id: str, body: Label, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    _emit(request, conn, [("album.add", {"photo_id": photo_id, "album": body.value})])
    return media_detail(conn, photo_id)


@router.delete("/media/{photo_id}/albums/{album}")
def remove_album(photo_id: str, album: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    seen = _live_adds(conn, "media_albums", "album", photo_id, " ".join(album.split()))
    if seen:
        _emit(request, conn, [("album.remove", {"photo_id": photo_id, "album": album, "observed_add_ops": seen})])
    return media_detail(conn, photo_id)


@router.post("/media/{photo_id}/people")
def add_person(photo_id: str, body: PersonAdd, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    items = []
    pid = body.person_id
    if pid is None:
        if not body.name:
            raise HTTPException(422, "Give a person_id or a name")
        pid = person_id(body.name)
        if conn.execute("SELECT 1 FROM people WHERE person_id = ?", (pid,)).fetchone() is None:
            items.append(("person.create", {"person_id": pid, "name": body.name}))
    elif conn.execute("SELECT 1 FROM people WHERE person_id = ?", (pid,)).fetchone() is None:
        raise HTTPException(404, "No such person")
    items.append(("media.person.add", {"photo_id": photo_id, "person_id": resolve_person(conn, pid)}))
    _emit(request, conn, items)
    return media_detail(conn, photo_id)


@router.delete("/media/{photo_id}/people/{person}")
def remove_person(photo_id: str, person: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    _exists(conn, photo_id)
    survivor = resolve_person(conn, person)
    rows = conn.execute(f"SELECT person_id, add_op FROM media_people WHERE photo_id = ? AND {LIVE}", (photo_id,))
    items = [("media.person.remove", {"photo_id": photo_id, "person_id": p, "observed_add_ops": [a]})
             for p, a in rows if resolve_person(conn, p) == survivor]
    if items:
        _emit(request, conn, items)
    return media_detail(conn, photo_id)


@router.patch("/people/{person}")
def rename_person(person: str, body: PersonPatch, request: Request, conn: sqlite3.Connection = Depends(db)):
    if conn.execute("SELECT 1 FROM people WHERE person_id = ?", (person,)).fetchone() is None:
        raise HTTPException(404, "No such person")
    _emit(request, conn, [("person.set", {"person_id": person, "field": "name", "value": body.name})])
    return dict(conn.execute("SELECT person_id, name FROM people WHERE person_id = ?", (person,)).fetchone())


@router.post("/people/{person}/merge")
def merge_person(person: str, body: Merge, request: Request, conn: sqlite3.Connection = Depends(db)):
    for p in (person, body.into):
        if conn.execute("SELECT 1 FROM people WHERE person_id = ?", (p,)).fetchone() is None:
            raise HTTPException(404, f"No such person: {p}")
    if resolve_person(conn, person) == resolve_person(conn, body.into):
        raise HTTPException(409, "They're already the same person")
    _emit(request, conn, [("person.merge", {"from": person, "into": body.into})])
    return {"person_id": person, "merged_into": body.into}
