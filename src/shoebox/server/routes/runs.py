"""Start, watch and cancel archive runs from the app (LOCAL_APP_PLAN.md §9.2)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ... import paths
from ...archiver import Options, Selection
from ...runlock import AlreadyRunning
from ..deps import bucket, user
from ..runs import history, summarize

router = APIRouter(prefix="/api", dependencies=[Depends(user)])


class AppleRun(BaseModel):
    mode: str = Field(pattern="^(selected|album|range|all|uuids)$")
    uuids: list[str] = Field(default_factory=list, max_length=10_000)
    album: str | None = Field(None, max_length=200)
    date_from: date | None = Field(None, alias="from")
    date_to: date | None = Field(None, alias="to")
    limit: int | None = Field(None, ge=1, le=100_000)
    dry_run: bool = False
    include_videos: bool = True


def options_for_app(app, *, dry_run: bool = False) -> Options:
    from ..deps import app_bucket

    s3 = app_bucket(app)
    workers = max(1, min(int(app.state.settings.workers or 4), 16))
    return Options(out=app.state.settings.library, bucket=None if dry_run else s3, dry_run=dry_run,
                   local_only=s3 is None and not dry_run, workers=workers)


def options_for(request: Request, *, dry_run: bool = False) -> Options:
    return options_for_app(request.app, dry_run=dry_run)


def _busy(e: AlreadyRunning) -> HTTPException:
    return HTTPException(409, {"message": "An archive run is already in progress", "run": e.holder})


@router.post("/uploads/apple-photos", status_code=202)
def start_apple_run(body: AppleRun, request: Request):
    if body.mode == "album" and not body.album:
        raise HTTPException(422, "Pick an album")
    if body.mode == "range" and not (body.date_from or body.date_to):
        raise HTTPException(422, "Give a start or end date")
    if body.mode == "uuids" and not body.uuids:
        raise HTTPException(422, "No items to retry")
    sel = Selection(selected=body.mode == "selected", albums=(body.album,) if body.mode == "album" else (),
                    from_date=datetime.combine(body.date_from, datetime.min.time()) if body.date_from else None,
                    to_date=datetime.combine(body.date_to, datetime.min.time()) if body.date_to else None,
                    all=body.mode == "all", limit=body.limit, include_videos=body.include_videos,
                    uuids=tuple(body.uuids) if body.mode == "uuids" else ())
    label = {"selected": "Selected in Photos", "album": f"Album: {body.album}", "all": "Everything not yet archived",
             "range": f"Taken {body.date_from or '…'} to {body.date_to or '…'}",
             "uuids": f"Retry {len(body.uuids)} item(s)"}[body.mode]
    try:
        run = request.app.state.runs.start("apple-photos", label + (" (dry run)" if body.dry_run else ""),
                                           options_for(request, dry_run=body.dry_run), selection=sel,
                                           device=request.app.state.device)
    except AlreadyRunning as e:
        raise _busy(e) from None
    except Exception as e:
        raise HTTPException(422, str(e)) from None
    return run.public()


@router.get("/runs/active")
def active_run(request: Request):
    """The run in progress on this Mac, if any (started here or from the terminal)."""
    live = request.app.state.runs.active()
    if live:
        return live.public()
    for h in history(limit=3):
        if h["status"] == "running":
            return h
    return None


@router.get("/runs")
def runs(request: Request):
    live = {r.run_id: r.public() for r in request.app.state.runs.runs.values()}
    out = []
    for h in history():
        out.append(live.pop(h["run_id"], None) or h)
    return list(live.values()) + out


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request):
    live = request.app.state.runs.get(run_id)
    f = paths.runs_dir() / f"{run_id}.jsonl"
    if live is None and not f.exists():
        raise HTTPException(404, "No such run")
    base = summarize(f) if f.exists() else {}
    return base | (live.public() if live else {})


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    live = request.app.state.runs.get(run_id)
    if live is None:
        raise HTTPException(404, "Only runs started from the app can be cancelled here; stop a terminal run with Ctrl-C")
    if live.status != "running":
        raise HTTPException(409, "That run has already finished")
    live.cancel.set()
    return {"run_id": run_id, "cancelling": True}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    """Server-sent events: everything the run has logged so far, then new lines as they arrive."""
    f = paths.runs_dir() / f"{run_id}.jsonl"
    if not f.exists():
        raise HTTPException(404, "No such run")

    async def stream():
        pos, ended_at, idle = 0, None, 0.0
        while True:
            if await request.is_disconnected():
                return
            with open(f, encoding="utf-8") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
            for line in chunk.splitlines():
                if line.strip():
                    yield f"data: {line}\n\n"
                    if json.loads(line).get("event") == "end":
                        ended_at = asyncio.get_event_loop().time()
            if ended_at and asyncio.get_event_loop().time() - ended_at > 3:   # trailing sync/backup messages
                live = request.app.state.runs.get(run_id)
                if live is None or live.status != "running":
                    yield "event: close\ndata: {}\n\n"
                    return
            idle += 0.5
            if not chunk and idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
