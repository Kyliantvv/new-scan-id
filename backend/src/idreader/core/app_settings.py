"""Réglages modifiables depuis l'administration (table `settings`), avec repli sur `.env`.

Seuls ces réglages sont exposés ; les secrets et chemins restent dans `.env`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from idreader.core.config import Settings
from idreader.core.models import AppSetting


class AppSettings(BaseModel):
    auto_validate_threshold: float = Field(ge=0.8, le=1.0)
    retention_images_days: int = Field(ge=1, le=3650)
    retention_data_days: int = Field(ge=1, le=3650)
    # Bouton « Ajouter au jeu de test » (désactivé par défaut, 07_ROADMAP.md phase 3).
    golden_enabled: bool = False


def defaults(settings: Settings) -> AppSettings:
    return AppSettings(
        auto_validate_threshold=settings.auto_validate_threshold,
        retention_images_days=settings.retention_images_days,
        retention_data_days=settings.retention_data_days,
    )


def load(db: Session, settings: Settings) -> AppSettings:
    values = defaults(settings).model_dump()
    for row in db.query(AppSetting).filter(AppSetting.key.in_(list(values))):
        values[row.key] = row.value_json
    return AppSettings.model_validate(values)


def save(db: Session, new: AppSettings) -> None:
    for key, value in new.model_dump().items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value_json=value))
        else:
            row.value_json = value
    db.flush()
