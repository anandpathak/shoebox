"""Which of these files does the archive already have? (by SHA-256 of the original; LOCAL_APP_PLAN.md §13.4)"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import paths
from ..deps import db, user

router = APIRouter(prefix="/api/backup", dependencies=[Depends(user)])
HEX = re.compile(r"^[0-9a-f]{64}$")


class Check(BaseModel):
    hashes: list[str] = Field(max_length=500)


@router.post("/check")
def check(body: Check, conn: sqlite3.Connection = Depends(db)):
    wanted = [h.lower() for h in body.hashes]
    if any(not HEX.match(h) for h in wanted):
        raise HTTPException(422, "hashes must be hex SHA-256 values")
    have = set()
    for i in range(0, len(wanted), 400):
        chunk = wanted[i:i + 400]
        have |= {r[0] for r in conn.execute(f"SELECT sha256 FROM media WHERE sha256 IN ({','.join('?' * len(chunk))})",
                                            chunk)}
    pending = _inbox_hashes()
    return {"missing": [h for h in wanted if h not in have and h not in pending],
            "archived": [h for h in wanted if h in have],
            "in_progress": [h for h in wanted if h in pending and h not in have]}


def _inbox_hashes() -> set[str]:
    """Uploads that arrived but aren't archived yet don't need sending again."""
    import hashlib

    out = set()
    for f in paths.inbox_dir().glob("*/*__*"):
        cache = f.with_name(f.name + ".sha256")
        if cache.exists():
            out.add(cache.read_text().strip())
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        cache.write_text(h.hexdigest())
        out.add(h.hexdigest())
    return out
