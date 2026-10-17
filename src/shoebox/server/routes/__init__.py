"""API routes, one module per area."""

from __future__ import annotations

from fastapi import FastAPI


def _album_catch_up(app) -> None:
    """At start: compare the archive with the real "Archived to S3" album and add what's missing (after a
    crash, a restart, or a Photos hiccup). Only if anything from Apple Photos was archived on this Mac."""
    from ...state import State

    try:
        if not State().apple_archived():
            return
    except Exception:  # noqa: BLE001
        return
    app.state.album.start()


def register(app: FastAPI) -> None:
    from . import album, backupcheck, backups, catalogs, faces, native, viewer, edit, library, originals, proxy, push, runs, sync, tus
    from ..runs import RunManager

    app.state.runs = RunManager()
    app.state.inbox = tus.InboxProcessor(app)
    app.state.album = album.AlbumJob()
    from ..analysis import AnalysisWorker
    app.state.analysis = AnalysisWorker(app)
    app.state.runs.on_finished.append(app.state.analysis.kick)     # new photos → labels and faces

    def start_inbox():
        tus.tidy_inbox()
        if app.state.settings.album_catch_up:
            _album_catch_up(app)
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
    app.include_router(album.router)
    from .. import pairing
    app.include_router(pairing.router)
    sync.install_background_sync(app)
