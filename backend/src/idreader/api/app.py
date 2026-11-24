"""Application FastAPI : routes /api, en-têtes de sécurité, front servi sur la même origine."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from idreader import __version__
from idreader.api import auth, batches, events
from idreader.core.config import Settings, get_settings
from idreader.core.db import make_engine, make_session_factory
from idreader.core.logging import log_exception_safely

log = logging.getLogger("idreader.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Aucune ressource externe (CLAUDE.md, règle 1). AG Grid injecte des styles en ligne.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="IDReader",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    engine = make_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.read_session_factory = make_session_factory(engine, immediate=False)

    @app.middleware("http")
    async def access_log_and_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # erreur inattendue : jamais le message (données possibles)
            log_exception_safely(log, exc, f"request {request.method} {request.url.path}")
            response = JSONResponse(
                {"detail": {"code": "INTERNAL_ERROR", "message": "Erreur interne."}},
                status_code=500,
            )
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        # Journal d'accès maison : chemin sans la chaîne de requête (q=… peut contenir un nom).
        log.info(
            "%s %s %s %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # On ne renvoie pas les valeurs reçues (elles peuvent contenir des données personnelles).
        fields = [".".join(str(p) for p in err.get("loc", ())) for err in exc.errors()]
        return JSONResponse(
            {
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "Requête invalide.",
                    "fields": fields,
                }
            },
            status_code=422,
        )

    app.include_router(auth.router, prefix="/api")
    app.include_router(batches.router, prefix="/api")
    app.include_router(events.router, prefix="/api")

    @app.get("/api/health", tags=["admin"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    _mount_frontend(app, settings.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Sert le build Vite (même origine que l'API : pas de CORS)."""
    index = dist / "index.html"
    if not index.is_file():
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse(
                {"detail": {"code": "NOT_FOUND", "message": "Ressource introuvable."}},
                status_code=404,
            )
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
