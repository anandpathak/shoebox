"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import catalogs, edit, library, originals, proxy, sync

    app.include_router(library.router)
    app.include_router(edit.router)
    app.include_router(originals.router)
    app.include_router(sync.router)
    app.include_router(proxy.router)
    app.include_router(catalogs.router)
    sync.install_background_sync(app)
