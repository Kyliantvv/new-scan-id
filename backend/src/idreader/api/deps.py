"""Dépendances FastAPI : base, identité, CSRF, rôles."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from idreader.core.config import Settings
from idreader.core.db import utcnow
from idreader.core.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthContext,
    csrf_matches,
    resolve_session,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def api_error(status: int, code: str, message: str) -> HTTPException:
    """Erreur d'API : code stable + message en français, jamais de donnée personnelle."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    return factory


def get_db(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Iterator[Session]:
    with factory() as db:
        yield db


def current_auth(
    request: Request,
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthContext:
    """Identité de la requête ; contrôle CSRF pour toute méthode qui modifie des données.

    Utilise sa propre session courte : rien n'est verrouillé pendant le reste de la requête
    (important pour le flux SSE).
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    with factory() as db:
        auth = resolve_session(db, token, settings, utcnow())
        db.commit()
    if auth is None:
        raise api_error(401, "NOT_AUTHENTICATED", "Session absente ou expirée.")
    if request.method not in SAFE_METHODS and not csrf_matches(
        auth.csrf_token, request.headers.get(CSRF_HEADER)
    ):
        raise api_error(403, "CSRF_FAILED", "Jeton CSRF manquant ou invalide.")
    return auth


def require_admin(auth: Annotated[AuthContext, Depends(current_auth)]) -> AuthContext:
    if not auth.is_admin:
        raise api_error(403, "FORBIDDEN", "Action réservée aux administrateurs.")
    return auth


DbDep = Annotated[Session, Depends(get_db)]
AuthDep = Annotated[AuthContext, Depends(current_auth)]
AdminDep = Annotated[AuthContext, Depends(require_admin)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
