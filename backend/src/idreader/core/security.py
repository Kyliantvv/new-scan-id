"""Mots de passe (argon2id), sessions serveur, CSRF, verrouillage.

Référence : docs/05_SECURITE_RGPD.md § Accès.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from idreader.core.config import Settings
from idreader.core.models import Role, User, UserSession

_hasher = PasswordHasher()  # argon2id, paramètres par défaut d'argon2-cffi (RFC 9106)
# Empreinte factice : vérifiée quand l'e-mail est inconnu, pour un temps de réponse comparable.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SESSION_COOKIE = "idr_session"
CSRF_HEADER = "X-CSRF-Token"
LAST_SEEN_RESOLUTION = timedelta(seconds=60)


class AuthError(Exception):
    """Échec d'authentification avec un code stable (jamais de donnée dans le message)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str, settings: Settings) -> None:
    """12 caractères minimum (docs/05_SECURITE_RGPD.md)."""
    if len(password) < settings.password_min_length:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {settings.password_min_length} caractères."
        )


def hash_token(token: str, settings: Settings) -> str:
    """Empreinte HMAC-SHA256 du jeton de session : la base ne contient jamais le jeton."""
    key = settings.secret_key.get_secret_value().encode()
    if not key:
        raise RuntimeError("IDR_SECRET_KEY non défini")
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def create_user(db: Session, email: str, password: str, role: Role, settings: Settings) -> User:
    check_password_policy(password, settings)
    email = normalize_email(email)
    if "@" not in email:
        raise ValueError("Adresse e-mail invalide.")
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise ValueError("Un compte existe déjà avec cette adresse.")
    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, email: str, password: str, settings: Settings, now: datetime) -> User:
    """Vérifie les identifiants et applique le verrouillage après N échecs."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        raise AuthError("INVALID_CREDENTIALS")
    if user.locked_until is not None and user.locked_until > now:
        raise AuthError("ACCOUNT_LOCKED")
    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_logins = 0
        raise AuthError("INVALID_CREDENTIALS")
    if not user.active:
        raise AuthError("ACCOUNT_DISABLED")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


@dataclass(frozen=True)
class AuthContext:
    """Identité de la requête, détachée de la session ORM."""

    user_id: str
    email: str
    role: str
    session_id: str
    csrf_token: str

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


def open_session(db: Session, user: User, settings: Settings, now: datetime) -> tuple[str, str]:
    """Crée une session serveur. Renvoie (jeton du cookie, jeton CSRF)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=hash_token(token, settings),
            user_id=user.id,
            csrf_token=csrf,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_max_hours),
        )
    )
    return token, csrf


def resolve_session(
    db: Session, token: str, settings: Settings, now: datetime
) -> AuthContext | None:
    """Session valide : non expirée (8 h), active depuis moins de 30 min, compte actif."""
    if not token:
        return None
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token, settings))
    ).one_or_none()
    if row is None:
        return None
    sess, user = row
    idle_limit = sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if sess.expires_at <= now or idle_limit <= now or not user.active:
        db.delete(sess)
        return None
    if now - sess.last_seen_at >= LAST_SEEN_RESOLUTION:
        sess.last_seen_at = now
    return AuthContext(
        user_id=user.id,
        email=user.email,
        role=user.role,
        session_id=sess.id,
        csrf_token=sess.csrf_token,
    )


def close_session(db: Session, session_id: str) -> None:
    db.execute(delete(UserSession).where(UserSession.id == session_id))


def csrf_matches(expected: str, received: str | None) -> bool:
    return received is not None and hmac.compare_digest(expected, received)
