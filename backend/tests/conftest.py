"""Fixtures communes. Aucune vraie pièce d'identité : uniquement des données synthétiques."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from idreader.core.config import Settings, get_settings

TEST_SECRET = "test-secret-key-not-for-production"  # noqa: S105


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Réglages isolés : base et fichiers dans un dossier temporaire."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("IDR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("IDR_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("IDR_COOKIE_SECURE", "false")
    monkeypatch.setenv("IDR_DUMMY_JOB_SECONDS", "0")
    monkeypatch.setenv("IDR_SSE_POLL_SECONDS", "0.05")
    monkeypatch.setenv("IDR_IDVISION_PATH", str(tmp_path / "absent-idvision"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
