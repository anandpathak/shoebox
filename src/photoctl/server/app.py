"""photoctl's HTTP API and web app (LOCAL_APP_PLAN.md §8)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ..device import load_device
from .auth import COOKIE, User, get_or_create_token, may_set_cookie
from .config import Settings
from .deps import user


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [start() for start in app.state.startup_tasks]
        yield
        for stop in tasks:
            if stop:
                await stop()

    # No public /docs or /openapi.json: the schema is dumped at build time (scripts/dump_openapi.py).
    app = FastAPI(title="photoctl", version="1", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.token = get_or_create_token()
    app.state.device = load_device()
    app.state.startup_tasks = []

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return resp

    @app.get("/api/health")
    def health(u: User = Depends(user)):
        dev = app.state.device
        return {"ok": True, "user": u.name, "via": u.via,
                "device": {"device_id": dev.device_id, "name": dev.name, "platform": dev.platform},
                "s3": bool(settings.bucket)}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request):
        resp = _index_response(settings)
        if may_set_cookie(request):
            resp.set_cookie(COOKIE, app.state.token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=60 * 60 * 24 * 365)
        return resp

    from . import routes
    routes.register(app)
    if settings.static_dir and Path(settings.static_dir).exists():
        import mimetypes

        from fastapi.staticfiles import StaticFiles

        mimetypes.add_type("application/manifest+json", ".webmanifest")

        # Built web app (JS/CSS/icons/manifest). Registered last so /api and / win.
        app.mount("/", StaticFiles(directory=settings.static_dir), name="web")
    return app


def _index_response(settings: Settings) -> Response:
    if settings.static_dir and (Path(settings.static_dir) / "index.html").exists():
        return HTMLResponse((Path(settings.static_dir) / "index.html").read_text())
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>photoctl</title>"
                        "<p>The web app isn't built yet. Run <code>npm run build</code> in <code>web/</code>.</p>")


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)
