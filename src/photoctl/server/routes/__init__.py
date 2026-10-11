"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import edit, library, originals

    app.include_router(library.router)
    app.include_router(edit.router)
    app.include_router(originals.router)
