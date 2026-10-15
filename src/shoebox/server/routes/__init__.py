"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from . import backupcheck, backups, catalogs, faces, native, viewer, edit, library, originals, proxy, push, runs, sync, tus
    from ..runs import RunManager

    app.state.runs = RunManager()
    app.state.inbox = tus.InboxProcessor(app)
    from ..analysis import AnalysisWorker
    app.state.analysis = AnalysisWorker(app)
    app.state.runs.on_finished.append(app.state.analysis.kick)     # new photos → labels and faces

    def start_inbox():
        if app.state.settings.bucket:
            app.state.inbox.requeue_local()
        if app.state.inbox.pending():
            app.state.inbox.kick()
        app.state.analysis.kick()          # catch up on anything not analysed yet

        async def stop():
            app.state.analysis.stop()
        return stop
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
    app.include_router(backupcheck.router)
    app.include_router(native.router)
    app.include_router(faces.router)
    app.include_router(viewer.router)
    from .. import pairing
    app.include_router(pairing.router)
    sync.install_background_sync(app)
