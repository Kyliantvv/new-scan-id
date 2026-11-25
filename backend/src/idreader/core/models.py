"""Modèles ORM. Référence : docs/02_ARCHITECTURE.md § Modèle de données."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from idreader.core.db import UTCDateTime, new_id, utcnow


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime(), dict[str, Any]: JSON, list[Any]: JSON}  # noqa: RUF012


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Role(StrEnum):
    OPERATOR = "operator"
    ADMIN = "admin"


class BatchStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class DocumentStatus(StrEnum):
    PENDING = "pending"  # En attente
    PROCESSING = "processing"  # En cours
    AUTO_VALIDATED = "auto_validated"  # Validé (auto)
    TO_REVIEW = "to_review"  # À vérifier
    MANUAL_VALIDATED = "manual_validated"  # Validé (manuel)
    FAILED = "failed"  # Échec
    DUPLICATE = "duplicate"  # Doublon : fichier identique (même SHA-256) uniquement


class FieldStatus(StrEnum):
    OK = "ok"
    REPAIRED = "repaired"
    UNCERTAIN = "uncertain"  # une seule source, sans preuve : à vérifier
    CONFLICT = "conflict"
    MISSING = "missing"
    MANUAL = "manual"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobType(StrEnum):
    PROCESS_FILE = "process_file"
    PAIR_BATCH = "pair_batch"
    REPROCESS_DOCUMENT = "reprocess_document"
    PURGE = "purge"


class Side(StrEnum):
    FRONT = "front"
    BACK = "back"
    UNKNOWN = "unknown"


class PhysicalFormat(StrEnum):
    ID1 = "ID1"
    ID2 = "ID2"
    ID3 = "ID3"
    UNKNOWN = "unknown"


# Champs extraits (lignes de `fields`) ; la vue `documents_flat` les met à plat.
FIELD_NAMES: tuple[str, ...] = (
    "surname",
    "given_names",
    "usage_name",
    "sex",
    "date_of_birth",
    "place_of_birth",
    "nationality",
    "document_number",
    "date_of_issue",
    "date_of_expiry",
    "authority",
    "personal_number",
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", Role), name="ck_users_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.OPERATOR)
    active: Mapped[bool] = mapped_column(default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None]


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (CheckConstraint(_in("status", BatchStatus), name="ck_batches_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default=BatchStatus.OPEN)
    counts_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("batch_id", "upload_order", name="uq_files_batch_order"),
        Index("ix_files_batch_sha256", "batch_id", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    original_name: Mapped[str] = mapped_column(String(255))  # jamais dans les logs
    mime: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    storage_path: Mapped[str] = mapped_column(String(512))  # relatif à IDR_DATA_DIR
    upload_order: Mapped[int]
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    page_index: Mapped[int]
    storage_path: Mapped[str] = mapped_column(String(512))
    width: Mapped[int]
    height: Mapped[int]
    quality_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Crop(Base):
    __tablename__ = "crops"
    __table_args__ = (
        CheckConstraint(_in("side", Side), name="ck_crops_side"),
        CheckConstraint(_in("physical_format", PhysicalFormat), name="ck_crops_physical_format"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    quad_json: Mapped[list[Any]] = mapped_column(default=list)
    storage_path: Mapped[str] = mapped_column(String(512))
    width: Mapped[int]
    height: Mapped[int]
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    side: Mapped[str] = mapped_column(String(8), default=Side.UNKNOWN)
    # Reste « unknown » tant que le format de MRZ n'a pas tranché (docs/DECISIONS.md).
    physical_format: Mapped[str] = mapped_column(String(8), default=PhysicalFormat.UNKNOWN)
    has_mrz: Mapped[bool] = mapped_column(default=False)
    quality_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(_in("status", DocumentStatus), name="ck_documents_status"),
        Index("ix_documents_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.PENDING)
    status_reasons_json: Mapped[list[Any]] = mapped_column(default=list)
    doc_type: Mapped[str | None] = mapped_column(String(32))
    doc_code: Mapped[str | None] = mapped_column(String(2))
    issuing_state: Mapped[str | None] = mapped_column(String(3))
    mrz_format: Mapped[str | None] = mapped_column(String(16))
    mrz_lines_json: Mapped[list[Any]] = mapped_column(default=list)
    mrz_valid: Mapped[bool | None]
    confidence: Mapped[float | None] = mapped_column(Float)
    alerts_json: Mapped[list[Any]] = mapped_column(default=list)
    front_crop_id: Mapped[str | None] = mapped_column(ForeignKey("crops.id", ondelete="SET NULL"))
    back_crop_id: Mapped[str | None] = mapped_column(ForeignKey("crops.id", ondelete="SET NULL"))
    # Doublon de document (pays + n° + date de naissance) : alerte + lien vers l'original.
    duplicate_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    # Association recto/verso suggérée (score < seuil automatique ou concurrent proche, étape J).
    # Pas de clé étrangère : ajoutée par ALTER TABLE sous SQLite ; nettoyée par l'appariement.
    suggested_pair_id: Mapped[str | None] = mapped_column(String(36))
    pair_score: Mapped[float | None] = mapped_column(Float)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class DocumentField(Base):
    __tablename__ = "fields"
    __table_args__ = (
        UniqueConstraint("document_id", "name", name="uq_fields_document_name"),
        CheckConstraint(_in("status", FieldStatus), name="ck_fields_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text)
    value_native: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=FieldStatus.MISSING)
    evidence_json: Mapped[list[Any]] = mapped_column(default=list)
    candidates_json: Mapped[list[Any]] = mapped_column(default=list)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JobStatus), name="ck_jobs_status"),
        CheckConstraint(_in("type", JobType), name="ck_jobs_type"),
        Index("ix_jobs_pick", "status", "priority", "run_after"),
        Index("ix_jobs_batch", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(36))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))  # code stable, jamais de données
    worker_id: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    run_after: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)


def documents_flat_sql() -> str:
    """Vue à plat (une colonne par champ) qui sert le tableau et les exports."""
    pivots = ",\n  ".join(
        f"MAX(CASE WHEN f.name = '{name}' THEN f.value END) AS {name}" for name in FIELD_NAMES
    )
    return (
        "CREATE VIEW documents_flat AS\n"
        "SELECT d.id, d.batch_id, d.status, d.status_reasons_json, d.doc_type, d.doc_code,\n"
        "  d.issuing_state, d.mrz_format, d.mrz_valid, d.confidence, d.alerts_json,\n"
        "  d.duplicate_of_id, d.created_at, d.updated_at,\n"
        "  MAX(CASE WHEN f.name = 'surname' THEN f.value_native END) AS surname_native,\n"
        f"  {pivots}\n"
        "FROM documents d LEFT JOIN fields f ON f.document_id = d.id\n"
        "GROUP BY d.id"
    )
