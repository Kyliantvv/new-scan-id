"""Administration : réglages, comptes, statistiques, santé (réservé aux administrateurs)."""

from __future__ import annotations

import logging
import platform
import shutil
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from idreader import __version__
from idreader.api.deps import AdminDep, DbDep, SettingsDep, api_error
from idreader.core import app_settings, audit
from idreader.core import models as m
from idreader.core.security import check_password_policy, create_user, hash_password
from idreader.pipeline.consolidate import REASON_LABELS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# Champs dont une correction renseigne la matrice de confusion MRZ (lus dans la MRZ).
MRZ_FIELDS = frozenset(
    {"surname", "given_names", "document_number", "date_of_birth", "date_of_expiry",
     "nationality", "sex", "personal_number"}
)  # fmt: skip


# --- Réglages -----------------------------------------------------------------------------


@router.get("/settings")
def get_settings(_auth: AdminDep, db: DbDep, settings: SettingsDep) -> app_settings.AppSettings:
    return app_settings.load(db, settings)


@router.put("/settings")
def put_settings(
    body: app_settings.AppSettings, auth: AdminDep, db: DbDep, settings: SettingsDep
) -> app_settings.AppSettings:
    before = app_settings.load(db, settings)
    app_settings.save(db, body)
    audit.record(
        db, "settings_changed", user_id=auth.user_id, entity="settings",
        before=before.model_dump(), after=body.model_dump(),
    )  # fmt: skip
    db.commit()
    log.info("settings changed")
    return app_settings.load(db, settings)


# --- Comptes ------------------------------------------------------------------------------


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    created_at: datetime


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(max_length=200)
    role: Literal["operator", "admin"] = "operator"


class UserPatch(BaseModel):
    role: Literal["operator", "admin"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=200)


def _user_out(u: m.User) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role, is_active=u.active, created_at=u.created_at)


@router.get("/users")
def list_users(_auth: AdminDep, db: DbDep) -> list[UserOut]:
    return [_user_out(u) for u in db.scalars(select(m.User).order_by(m.User.created_at))]


@router.post("/users", status_code=201)
def add_user(body: UserIn, auth: AdminDep, db: DbDep, settings: SettingsDep) -> UserOut:
    try:
        user = create_user(db, body.email, body.password, m.Role(body.role), settings)
    except ValueError as exc:
        raise api_error(422, "INVALID_USER", str(exc)) from exc
    audit.record(
        db, "user_created", user_id=auth.user_id, entity="user", entity_id=user.id,
        after={"role": user.role},
    )  # fmt: skip
    db.commit()
    log.info("user created user_id=%s", user.id)
    return _user_out(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str, body: UserPatch, auth: AdminDep, db: DbDep, settings: SettingsDep
) -> UserOut:
    user = db.get(m.User, user_id)
    if user is None:
        raise api_error(404, "USER_NOT_FOUND", "Compte introuvable.")
    if user.id == auth.user_id and (body.is_active is False or body.role == "operator"):
        raise api_error(409, "SELF_LOCKOUT", "Vous ne pouvez pas retirer vos propres droits.")
    before = {"role": user.role, "active": user.active}
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.active = body.is_active
    if body.password is not None:
        try:
            check_password_policy(body.password, settings)
        except ValueError as exc:
            raise api_error(422, "WEAK_PASSWORD", str(exc)) from exc
        user.password_hash = hash_password(body.password)
        user.failed_logins = 0
        user.locked_until = None
    if body.is_active is False or body.password is not None or body.role is not None:
        # Sessions ouvertes fermées : les nouveaux droits s'appliquent tout de suite.
        db.execute(delete(m.UserSession).where(m.UserSession.user_id == user.id))
    audit.record(
        db,
        "user_updated",
        user_id=auth.user_id,
        entity="user",
        entity_id=user.id,
        before=before,
        after={"role": user.role, "active": user.active, "reset": body.password is not None},
    )
    db.commit()
    log.info("user updated user_id=%s", user.id)
    return _user_out(user)


# --- Statistiques -------------------------------------------------------------------------


class StatsOut(BaseModel):
    documents: int
    by_status: dict[str, int]
    automation_pct: float | None
    failures_by_reason: dict[str, int]
    by_country: dict[str, int]
    by_type: dict[str, int]
    most_corrected_fields: list[tuple[str, int]]
    mrz_confusions: list[tuple[str, str, int]]
    avg_ms_per_document: float | None


def confusions(before: str, after: str) -> list[tuple[str, str]]:
    """Paires (lu, corrigé) caractère par caractère, si les longueurs sont égales."""
    if len(before) != len(after):
        return []
    return [(a, b) for a, b in zip(before, after, strict=True) if a != b]


@router.get("/stats")
def stats(_auth: AdminDep, db: DbDep, batch: Annotated[str | None, Query()] = None) -> StatsOut:
    docs = select(m.Document)
    if batch:
        docs = docs.where(m.Document.batch_id == batch)
    rows = db.scalars(docs).all()
    by_status = Counter(d.status for d in rows)
    analysed = sum(n for s, n in by_status.items() if s not in ("pending", "processing"))
    failures: Counter[str] = Counter()
    for d in rows:
        if d.status == m.DocumentStatus.FAILED:
            for r in d.status_reasons_json or [{"code": "UNKNOWN"}]:
                code = r.get("code", "UNKNOWN") if isinstance(r, dict) else str(r)
                failures[r.get("label") or REASON_LABELS.get(code, code)] += 1
    ids = {d.id for d in rows}
    corrected: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for entry in db.scalars(
        select(m.AuditLog).where(
            m.AuditLog.action == "field_corrected", m.AuditLog.entity == "document"
        )
    ):
        if batch and entry.entity_id not in ids:
            continue
        name = (entry.after_json or {}).get("field")
        if not name:
            continue
        corrected[name] += 1
        before = (entry.before_json or {}).get("value")
        after = (entry.after_json or {}).get("value")
        if name in MRZ_FIELDS and before and after:
            pairs.update(confusions(str(before), str(after)))
    jobs = select(
        func.avg((func.julianday(m.Job.finished_at) - func.julianday(m.Job.locked_at)) * 86_400_000)
    ).where(m.Job.type == m.JobType.PROCESS_FILE, m.Job.status == m.JobStatus.DONE)
    if batch:
        jobs = jobs.where(m.Job.batch_id == batch)
    avg_ms = db.scalar(jobs)
    return StatsOut(
        documents=len(rows),
        by_status=dict(by_status),
        automation_pct=round(100 * by_status.get("auto_validated", 0) / analysed, 1)
        if analysed
        else None,
        failures_by_reason=dict(failures.most_common()),
        by_country=dict(Counter(d.issuing_state or "?" for d in rows).most_common(30)),
        by_type=dict(Counter(d.doc_type or "other" for d in rows).most_common()),
        most_corrected_fields=corrected.most_common(12),
        mrz_confusions=[(a, b, n) for (a, b), n in pairs.most_common(20)],
        avg_ms_per_document=round(float(avg_ms), 0) if avg_ms is not None else None,
    )


# --- Santé --------------------------------------------------------------------------------


@router.get("/health")
def health(_auth: AdminDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    from idreader.engines.registry import available_engines, available_viz_engines

    usage = shutil.disk_usage(settings.data_dir)
    queue = dict(db.execute(select(m.Job.status, func.count()).group_by(m.Job.status)).all())
    return {
        "version": __version__,
        "platform": platform.platform(),
        "macos": platform.mac_ver()[0] or None,
        "engines_mrz": [e.name for e in available_engines(settings)],
        "engines_viz": [e.name for e in available_viz_engines(settings)],
        "queue": queue,
        "disk_free_gb": round(usage.free / 1e9, 1),
        "disk_total_gb": round(usage.total / 1e9, 1),
    }
