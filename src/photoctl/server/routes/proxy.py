"""Sync for clients without AWS credentials (phones, browsers): they push and pull ops through here.

Pull cursors are the server's local op sequence (SQLite rowid, i.e. arrival order), not HLCs: ops can
arrive here with an older HLC than ones already handed out. The catalog 'epoch' changes when this
catalog is rebuilt; a client holding a cursor from another epoch must re-download the catalog.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ...catalog import epoch
from ...catalog.apply import apply_ops
from ...catalog.ops import Emitter, InvalidOp, validate
from ..deps import db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
PLATFORMS = "^(ios|android|web|macos|linux|windows|unknown)$"


class PushOps(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    ops: list[dict] = Field(max_length=5000)


class Register(BaseModel):
    device_id: str = Field(pattern=r"^[0-9A-Za-z_.:-]{1,64}$")
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(pattern=PLATFORMS)


@router.post("/sync/ops")
def push_ops(body: PushOps, request: Request, conn: sqlite3.Connection = Depends(db)):
    if body.device_id == request.app.state.device.device_id:
        raise HTTPException(409, "That device id belongs to this server")
    for op in body.ops:
        try:
            validate(op)
        except InvalidOp as e:
            raise HTTPException(422, str(e)) from None
        if op["device_id"] != body.device_id:
            raise HTTPException(422, f"op {op['op_id']} was made by {op['device_id']}, not {body.device_id}")
    ids = [o["op_id"] for o in body.ops]
    known = {r[0] for r in conn.execute(
        f"SELECT op_id FROM ops WHERE op_id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    new = [o for o in body.ops if o["op_id"] not in known]
    with conn:
        apply_ops(conn, new, in_transaction=True, trusted=True)
        conn.executemany("INSERT OR IGNORE INTO outbox (op_id) VALUES (?)", [(o["op_id"],) for o in new])
    return {"accepted": len(new), "already_had": len(body.ops) - len(new)}


@router.get("/sync/ops")
def pull_ops(since: int = Query(0, ge=0), epoch_id: str | None = Query(None, alias="epoch"),
             limit: int = Query(1000, ge=1, le=5000), conn: sqlite3.Connection = Depends(db)):
    current = epoch(conn)
    top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM ops").fetchone()[0]
    if (epoch_id and epoch_id != current) or since > top:
        raise HTTPException(410, "This server's catalog was rebuilt. Download the catalog again.")
    rows = conn.execute("SELECT rowid, * FROM ops WHERE rowid > ? ORDER BY rowid LIMIT ?", (since, limit)).fetchall()
    ops = [{"op_id": r["op_id"], "hlc": r["hlc"], "device_id": r["device_id"], "kind": r["kind"],
            "entity": r["entity"], "body": json.loads(r["body"])} for r in rows]
    cursor = rows[-1]["rowid"] if rows else since
    return {"epoch": current, "ops": ops, "cursor": cursor, "more": cursor < top}


@router.post("/devices/register")
def register_device(body: Register, request: Request, conn: sqlite3.Connection = Depends(db)):
    Emitter(conn, request.app.state.device.device_id).emit(
        "device.register", {"device_id": body.device_id, "name": body.name, "platform": body.platform})
    return {"device_id": body.device_id, "name": body.name, "platform": body.platform,
            "server_device_id": request.app.state.device.device_id}
