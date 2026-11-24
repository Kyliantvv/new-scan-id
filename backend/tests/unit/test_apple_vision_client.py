"""Client du helper `idvision`, testé avec le faux helper (tests/fakes/fake_idvision.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from idreader.engines.apple_vision import (
    AppleVisionEngine,
    HelperCrashed,
    HelperError,
    HelperTimeout,
    HelperUnavailable,
    VisionHelperClient,
    probe_helper,
)
from idreader.engines.base import OcrEngine, Roi
from tests.helpers import TINY_PNG

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_idvision.py"


@pytest.fixture
def image(tmp_path: Path) -> Path:
    p = tmp_path / "crop.png"
    p.write_bytes(TINY_PNG)
    return p


@pytest.fixture
def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clients: list[VisionHelperClient] = []

    def factory(mode: str = "normal", timeout: float = 10.0) -> VisionHelperClient:
        monkeypatch.setenv("FAKE_IDVISION_MODE", mode)
        monkeypatch.setenv("FAKE_IDVISION_STATE", str(tmp_path / "state"))
        c = VisionHelperClient([sys.executable, str(FAKE), "serve"], timeout_seconds=timeout)
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.close()


def test_version_languages_doctor(make_client) -> None:
    c = make_client()
    assert c.version()["version"] == "fake"
    assert "fr-FR" in c.languages()["accurate"]
    assert c.doctor()["macos_version"] == [15, 0, 0]
    assert c.starts == 1  # un seul processus pour plusieurs requêtes


def test_engine_returns_typed_lines_in_pixels(make_client, image: Path) -> None:
    engine: OcrEngine = AppleVisionEngine(make_client())
    lines = engine.recognize(image, languages=["en-US"], top_candidates=5)
    assert len(lines) == 1
    line = lines[0]
    assert line.engine == "vision"
    assert line.text.startswith("P<UTO")
    assert line.quad.points[0] == (10.0, 20.0)
    assert line.quad.bbox == (10.0, 20.0, 880.0, 40.0)
    assert line.candidates[0].confidence == 0.5


def test_roi_sent_in_pixels(make_client, image: Path) -> None:
    engine = AppleVisionEngine(make_client())
    lines = engine.recognize(image, roi=Roi(x=100, y=500, width=800, height=150))
    assert lines[0].quad.points[0] == (110.0, 520.0)


def test_helper_error_code(make_client, tmp_path: Path) -> None:
    c = make_client()
    with pytest.raises(HelperError) as exc:
        c.recognize_text(tmp_path / "absent.png")
    assert exc.value.code == "IMAGE_NOT_FOUND"


def test_restart_after_crash_and_retry(make_client, image: Path) -> None:
    c = make_client("crash_once")
    response = c.recognize_text(image)
    assert response["ok"] is True
    assert c.starts == 2


def test_crash_twice_raises(make_client, image: Path) -> None:
    c = make_client("crash_always")
    with pytest.raises(HelperCrashed):
        c.recognize_text(image)
    # le client reste utilisable : il relancera un processus à la requête suivante
    assert c.version()["version"] == "fake"


def test_timeout_kills_and_recovers(make_client, image: Path) -> None:
    c = make_client("hang", timeout=1.0)
    with pytest.raises(HelperTimeout):
        c.recognize_text(image)
    assert c.version()["version"] == "fake"
    assert c.starts == 2


def test_protocol_error(make_client, image: Path) -> None:
    c = make_client("garbage")
    with pytest.raises(HelperCrashed) as exc:
        c.recognize_text(image)
    assert exc.value.code == "HELPER_PROTOCOL_ERROR"


def test_stale_reply_ignored(make_client, image: Path) -> None:
    c = make_client("stale_reply")
    assert c.recognize_text(image)["lines"]


def test_missing_binary(tmp_path: Path) -> None:
    c = VisionHelperClient([str(tmp_path / "absent-idvision"), "serve"])
    with pytest.raises(HelperUnavailable):
        c.version()


def test_probe_helper_absent(settings) -> None:
    assert probe_helper(settings) is None
