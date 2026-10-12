"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import backups, catalogs, edit, library, originals, proxy, push, runs, sync, tus
    from ..runs import RunManager

    app.state.runs = RunManager()
    app.state.inbox = tus.InboxProcessor(app)

    def start_inbox():
        if app.state.settings.bucket:
            app.state.inbox.requeue_local()
        if app.state.inbox.pending():
            app.state.inbox.kick()
        return None
    app.state.startup_tasks.append(start_inbox)

    app.include_router(library.router)
    app.include_router(edit.router)
    app.include_router(originals.router)
    app.include_router(sync.router)
    app.include_router(proxy.router)
    app.include_router(catalogs.router)
    app.include_router(backups.router)
    app.include_router(runs.router)
    app.include_router(tus.router)
    app.include_router(push.router)
    sync.install_background_sync(app)
