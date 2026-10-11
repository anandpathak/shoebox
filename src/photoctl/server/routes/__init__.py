"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import library

    app.include_router(library.router)
