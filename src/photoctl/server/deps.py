"""Shared FastAPI dependencies."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import HTTPException, Request

from ..catalog import open_catalog
from .auth import User, authenticate


def db() -> Iterator[sqlite3.Connection]:
    conn = open_catalog()
    try:
        yield conn
    finally:
        conn.close()


def user(request: Request) -> User:
    return authenticate(request)


def bucket(request: Request):
    """The archive bucket, or None when the server runs without S3 (local-only)."""
    app = request.app
    if getattr(app.state, "s3", None) is None and app.state.settings.bucket:
        from ..s3 import Bucket

        app.state.s3 = Bucket(app.state.settings.bucket, app.state.settings.profile)
    return getattr(app.state, "s3", None)


def require_bucket(request: Request):
    s3 = bucket(request)
    if s3 is None:
        raise HTTPException(503, "This server runs without S3. Start it with --bucket (or $PHOTOCTL_BUCKET).")
    return s3
