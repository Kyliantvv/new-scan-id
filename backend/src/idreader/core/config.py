"""Configuration de l'application (variables d'environnement `IDR_*` et fichier `.env`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/idreader/core/config.py → racine du dépôt
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Réglages lus dans l'environnement. Voir `docs/02_ARCHITECTURE.md` § Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="IDR_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = REPO_ROOT / "data"
    secret_key: SecretStr = SecretStr("")
    bind: str = "127.0.0.1:8000"
    workers: int = 1

    retention_images_days: int = 30
    retention_data_days: int = 365
    auto_validate_threshold: float = 0.95

    # Sessions (docs/05_SECURITE_RGPD.md § Accès)
    cookie_secure: bool = True
    session_max_hours: float = 8.0
    session_idle_minutes: float = 30.0
    login_max_failures: int = 5
    login_lock_minutes: float = 15.0
    password_min_length: int = 12

    # Uploads (docs/05_SECURITE_RGPD.md § Fichiers et uploads)
    max_upload_mb: int = 50

    # File de jobs (docs/02_ARCHITECTURE.md § File de jobs)
    heartbeat_seconds: float = 10.0
    stale_after_seconds: float = 60.0
    max_attempts: int = 3
    worker_poll_seconds: float = 1.0
    dummy_job_seconds: float = 2.0

    # SSE
    sse_poll_seconds: float = 1.0
    sse_max_seconds: float = 1800.0

    # Helper Swift
    idvision_path: Path = REPO_ROOT / "vision-helper" / ".build" / "release" / "idvision"
    idvision_timeout_seconds: float = 60.0

    tessdata_prefix: Path = Path("/opt/homebrew/share/tessdata")
    vlm_enabled: bool = False
    vlm_model: str = "mlx-community/PaddleOCR-VL-1.6-4bit"

    frontend_dist: Path = Field(default=REPO_ROOT / "frontend" / "dist")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "idreader.sqlite3"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Réglages du processus (mis en cache ; `get_settings.cache_clear()` dans les tests)."""
    return Settings()
