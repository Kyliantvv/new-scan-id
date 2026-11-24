from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from idreader.cli import app
from idreader.core.config import REPO_ROOT, Settings


def test_defaults_match_documentation(monkeypatch) -> None:
    for key in ("IDR_DATA_DIR", "IDR_COOKIE_SECURE", "IDR_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.bind == "127.0.0.1:8000"
    assert s.workers == 1
    assert s.retention_images_days == 30
    assert s.retention_data_days == 365
    assert s.auto_validate_threshold == 0.95
    assert s.cookie_secure is True
    assert s.heartbeat_seconds == 10
    assert s.stale_after_seconds == 60
    assert s.max_attempts == 3
    assert s.max_upload_mb == 50
    assert s.data_dir == REPO_ROOT / "data"


def test_env_override(settings: Settings, tmp_path: Path) -> None:
    assert settings.data_dir == tmp_path / "data"
    assert settings.cookie_secure is False
    assert settings.database_path == tmp_path / "data" / "idreader.sqlite3"
    assert settings.database_url.startswith("sqlite:///")


def test_repo_root_contains_claude_md() -> None:
    assert (REPO_ROOT / "CLAUDE.md").is_file()


def test_show_config_hides_secret(settings: Settings) -> None:
    result = CliRunner().invoke(app, ["show-config"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "secret_key" not in data
    assert data["secret_key_set"] is True
    assert "test-secret" not in result.output
