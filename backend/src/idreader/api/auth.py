"""Routes d'authentification : /api/auth/login, /api/auth/logout, /api/me."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from pydantic import BaseModel

from idreader.api.deps import AuthDep, DbDep, SettingsDep, api_error
from idreader.core import audit
from idreader.core.db import utcnow
from idreader.core.security import (
    SESSION_COOKIE,
    AuthError,
    authenticate,
    close_session,
    open_session,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

_AUTH_ERRORS = {
    "INVALID_CREDENTIALS": (401, "Adresse e-mail ou mot de passe incorrect."),
    "ACCOUNT_LOCKED": (429, "Trop d'échecs de connexion : compte verrouillé temporairement."),
    "ACCOUNT_DISABLED": (403, "Ce compte est désactivé."),
}


class LoginIn(BaseModel):
    email: str
    password: str


class MeOut(BaseModel):
    id: str
    email: str
    role: str
    csrf_token: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response, db: DbDep, settings: SettingsDep) -> MeOut:
    now = utcnow()
    try:
        user = authenticate(db, body.email, body.password, settings, now)
    except AuthError as exc:
        audit.record(db, "login_failed", user_id=None, entity="user")
        db.commit()  # garde le compteur d'échecs et le verrouillage
        log.info("login refused code=%s", exc.code)
        status, message = _AUTH_ERRORS[exc.code]
        raise api_error(status, exc.code, message) from None
    token, csrf = open_session(db, user, settings, now)
    audit.record(db, "login", user_id=user.id, entity="user", entity_id=user.id)
    db.commit()
    log.info("login ok user_id=%s", user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(settings.session_max_hours * 3600),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return MeOut(id=user.id, email=user.email, role=user.role, csrf_token=csrf)


@router.post("/auth/logout", status_code=204)
def logout(auth: AuthDep, response: Response, db: DbDep, settings: SettingsDep) -> None:
    close_session(db, auth.session_id)
    audit.record(db, "logout", user_id=auth.user_id, entity="user", entity_id=auth.user_id)
    db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="strict"
    )


@router.get("/me")
def me(auth: AuthDep) -> MeOut:
    return MeOut(id=auth.user_id, email=auth.email, role=auth.role, csrf_token=auth.csrf_token)
