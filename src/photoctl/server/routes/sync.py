"""Sync from the app: a background loop plus 'Sync now' and status."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from ...runlock import AlreadyRunning
from ...sync import full_sync
from ..deps import app_bucket, bucket, db, require_bucket, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_sync(app: FastAPI, s3) -> dict:
    status = app.state.sync_status
    status["running"] = True
    try:
        r = full_sync(s3, app.state.device)
        status.update(last_ok=_now(), error=None, last_result={
            k: v for k, v in asdict(r).items() if k in ("pushed_ops", "pushed_batches", "pulled_batches",
                                                        "applied_ops", "rejected_batches")})
        return status
    except AlreadyRunning:
        raise
    except Exception as e:  # report, keep the loop alive
        status.update(error=str(e), last_error_at=_now())
        log.warning("sync failed: %s", e)
        return status
    finally:
        status["running"] = False
        status["last_run"] = _now()


def install_background_sync(app: FastAPI) -> None:
    app.state.sync_status = {"running": False, "last_run": None, "last_ok": None, "error": None,
                             "last_result": None, "interval": app.state.settings.sync_interval}

    def start():
        if not app.state.settings.bucket or app.state.settings.sync_interval <= 0:
            return None

        async def loop():
            while True:
                try:
                    await asyncio.to_thread(run_sync, app, app_bucket(app))
                    from ..push import check_originals

                    await asyncio.to_thread(check_originals, app_bucket(app))
                except AlreadyRunning:
                    pass
                await asyncio.sleep(app.state.settings.sync_interval)

        task = asyncio.get_event_loop().create_task(loop())

        async def stop():
            task.cancel()
        return stop

    app.state.startup_tasks.append(start)


@router.post("/sync")
async def sync_now(request: Request, s3=Depends(require_bucket)):
    try:
        return await asyncio.to_thread(run_sync, request.app, s3)
    except AlreadyRunning:
        raise HTTPException(409, "A sync is already running") from None


@router.get("/sync/status")
def sync_status(request: Request, conn: sqlite3.Connection = Depends(db)):
    status = dict(request.app.state.sync_status)
    status["pending_ops"] = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    status["s3"] = bucket(request) is not None
    return status


@router.get("/sync/verify")
def sync_verify(request: Request, conn: sqlite3.Connection = Depends(db)):
    """Does every device that has synced hold the same library? (Uses each device's last registry entry.)"""
    from ...catalog.checksum import checksum
    from ...sync import list_registry

    mine = checksum(conn)
    s3 = bucket(request)
    entries = list_registry(s3) if s3 else []
    me = request.app.state.device.device_id
    devices = [{"device_id": e["device_id"], "name": e["name"], "platform": e["platform"],
                "last_seen": e.get("last_seen"), "pending_ops": e.get("pending_ops", 0), "media": e.get("media"),
                "same": e.get("checksum") == mine, "this_device": e["device_id"] == me} for e in entries]
    others = [d for d in devices if not d["this_device"]]
    return {"checksum": mine, "devices": devices, "converged": bool(others) and all(d["same"] for d in others),
            "s3": s3 is not None}
