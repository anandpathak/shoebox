"""Bring originals back from Deep Archive through the API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import originals
from ...fetch import presign
from ..deps import db, require_bucket, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])


class RestoreRequest(BaseModel):
    tier: str = Field("standard", pattern="^(standard|bulk)$")
    days: int = Field(7, ge=1, le=30)


def _with_link(s3, conn, info: dict) -> dict:
    out = {k: info[k] for k in ("photo_id", "status", "tier", "days", "requested_at", "ready_until", "checked_at")}
    out["eta_hours"] = originals.eta_hours(info["tier"].lower())
    if info["status"] in ("ready", "available"):
        name = conn.execute("SELECT orig_name FROM media WHERE photo_id = ?", (info["photo_id"],)).fetchone()[0]
        out["url"] = presign(s3, info["raw_key"], filename=name or Path(info["raw_key"]).name)
    return out


@router.post("/media/{photo_id}/original")
def request_original(photo_id: str, body: RestoreRequest, s3=Depends(require_bucket),
                     conn: sqlite3.Connection = Depends(db)):
    try:
        return _with_link(s3, conn, originals.request(s3, conn, photo_id, tier=body.tier, days=body.days))
    except LookupError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/media/{photo_id}/original")
def original_status(photo_id: str, s3=Depends(require_bucket), conn: sqlite3.Connection = Depends(db)):
    try:
        return _with_link(s3, conn, originals.status(s3, conn, photo_id))
    except LookupError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/originals")
def restore_requests(conn: sqlite3.Connection = Depends(db)):
    """Every original that has been requested, newest first (for a 'Requested originals' list)."""
    return [dict(r) for r in conn.execute("SELECT * FROM restore_requests ORDER BY requested_at DESC")]
