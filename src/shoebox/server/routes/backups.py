"""Back up this Mac's databases from the app (LOCAL_APP_PLAN.md §12)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ...backup import backup, last_local_backup, list_backups
from ..deps import require_bucket, user

router = APIRouter(prefix="/api/db", dependencies=[Depends(user)])


@router.post("/backup")
async def backup_now(request: Request, s3=Depends(require_bucket)):
    res = await asyncio.to_thread(backup, s3, request.app.state.device)
    return {"prefix": res.prefix, "monthly": res.monthly,
            "files": {k: {"bytes": v["bytes"], "sha256": v["sha256"]} for k, v in res.files.items()}}


@router.get("/backups")
def backups(request: Request, s3=Depends(require_bucket)):
    items = list_backups(s3, request.app.state.device.device_id)
    return {"last": last_local_backup(),
            "backups": [{"created_at": b["created_at"], "prefix": b["prefix"],
                         "bytes": sum(f["bytes"] for f in b["files"].values()), "files": sorted(b["files"]),
                         "catalog_checksum": (b.get("catalog") or {}).get("checksum")} for b in items]}
