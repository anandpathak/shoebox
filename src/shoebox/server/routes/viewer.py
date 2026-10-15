"""Hand the read-only S3 key to the phone app, so it can browse the library when no Mac is on."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ...viewer import HEAD_KEY, VIEWER_KEY, load_access
from ..deps import app_bucket, user

router = APIRouter(prefix="/api/viewer", dependencies=[Depends(user)])


@router.get("/access")
def access(request: Request, response: Response):
    """Only for people already allowed in (this Mac, the tailnet, paired phones). The key can only read
    thumbnails, small copies and the library file; it can't see originals or backups, or change anything."""
    bucket = app_bucket(request.app)
    if bucket is None:
        raise HTTPException(409, "This Mac runs without S3, so there's nothing for phones to read without it")
    got = load_access()
    if not got:
        raise HTTPException(404, "No read-only key set up yet. On the Mac: shoebox viewer access (see the README)")
    response.headers["Cache-Control"] = "no-store"
    endpoint = os.environ.get("AWS_ENDPOINT_URL")          # only set for local S3 stand-ins (tests)
    return {"bucket": bucket.name, "region": bucket.client.meta.region_name, "key_id": got["key_id"],
            "secret": got["secret"], "viewer": VIEWER_KEY, "head": HEAD_KEY,
            "endpoint": endpoint, "path_style": bool(endpoint)}
