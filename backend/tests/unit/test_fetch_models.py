"""scripts/fetch_models.py, testé avec des URL file:// : aucun accès réseau."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from idreader.core.config import REPO_ROOT

_spec = importlib.util.spec_from_file_location(
    "fetch_models", REPO_ROOT / "scripts" / "fetch_models.py"
)
assert _spec is not None and _spec.loader is not None
fm = importlib.util.module_from_spec(_spec)
sys.modules["fetch_models"] = fm  # requis par @dataclass
_spec.loader.exec_module(fm)

CONTENT = b"modele synthetique pour les tests\n" * 100
SHA = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    p = tmp_path / "source" / "modele.onnx"
    p.parent.mkdir()
    p.write_bytes(CONTENT)
    return p


def write_manifest(tmp_path: Path, url: str, sha: str = SHA, dest: str = "ppocr/test.onnx") -> Path:
    m = tmp_path / "models.toml"
    m.write_text(
        f'[[model]]\nname = "test"\nurl = "{url}"\nsha256 = "{sha}"\n'
        f'license = "Apache-2.0"\ndest = "{dest}"\n',
        encoding="utf-8",
    )
    return m


def run(*args: str) -> int:
    return fm.main(list(args))


def test_repository_manifest_is_valid() -> None:
    assert fm.load_manifest(REPO_ROOT / "scripts" / "models.toml") == []


def test_fetch_downloads_verifies_and_writes_manifest(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri())
    models = tmp_path / "models"
    assert run("--manifest", str(manifest), "--models-dir", str(models)) == 0
    target = models / "ppocr" / "test.onnx"
    assert target.read_bytes() == CONTENT
    installed = json.loads((models / "MANIFEST.json").read_text(encoding="utf-8"))
    assert installed["models"] == [
        {
            "name": "test",
            "file": "ppocr/test.onnx",
            "sha256": SHA,
            "size": len(CONTENT),
            "license": "Apache-2.0",
            "url": source.as_uri(),
        }
    ]
    # deuxième passage : rien à télécharger
    source.unlink()
    assert run("--manifest", str(manifest), "--models-dir", str(models)) == 0
    assert run("--verify", "--manifest", str(manifest), "--models-dir", str(models)) == 0


def test_wrong_hash_rejects_file(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri(), sha="0" * 64)
    models = tmp_path / "models"
    assert run("--manifest", str(manifest), "--models-dir", str(models)) == 1
    assert not (models / "ppocr" / "test.onnx").exists()
    assert list(models.rglob("*.part")) == []


def test_verify_refuses_any_difference(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri())
    models = tmp_path / "models"
    run("--manifest", str(manifest), "--models-dir", str(models))
    (models / "ppocr" / "test.onnx").write_bytes(CONTENT + b"x")
    assert run("--verify", "--manifest", str(manifest), "--models-dir", str(models)) == 1
    (models / "ppocr" / "test.onnx").unlink()
    assert run("--verify", "--manifest", str(manifest), "--models-dir", str(models)) == 1


def test_unpinned_model_never_installed(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri(), sha="")
    models = tmp_path / "models"
    assert run("--manifest", str(manifest), "--models-dir", str(models)) == 1
    assert not (models / "ppocr" / "test.onnx").exists()
    assert run("--verify", "--manifest", str(manifest), "--models-dir", str(models)) == 1


def test_pin_prints_hash_without_installing(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri(), sha="")
    models = tmp_path / "models"
    out = io.StringIO()
    errors = fm.pin(fm.load_manifest(manifest), models, out=out)
    assert errors == 0
    assert SHA in out.getvalue()
    assert "À REPORTER" in out.getvalue()
    assert not (models / "ppocr" / "test.onnx").exists()
    # le manifeste n'est jamais modifié par --pin
    assert 'sha256 = ""' in manifest.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("url", "dest"),
    [
        ("http://exemple.invalid/m.onnx", "m.onnx"),
        ("ftp://exemple.invalid/m.onnx", "m.onnx"),
        ("https://exemple.invalid/m.onnx", "../hors.onnx"),
        ("https://exemple.invalid/m.onnx", "/abs.onnx"),
    ],
)
def test_manifest_rejects_unsafe_entries(tmp_path: Path, url: str, dest: str) -> None:
    manifest = write_manifest(tmp_path, url, dest=dest)
    with pytest.raises(fm.FetchError):
        fm.load_manifest(manifest)
    assert run("--manifest", str(manifest), "--models-dir", str(tmp_path / "m")) == 2


def test_malformed_hash_rejected(tmp_path: Path, source: Path) -> None:
    with pytest.raises(fm.FetchError, match="mal formée"):
        fm.load_manifest(write_manifest(tmp_path, source.as_uri(), sha="abc"))


def test_unknown_name(tmp_path: Path, source: Path) -> None:
    manifest = write_manifest(tmp_path, source.as_uri())
    assert run("--manifest", str(manifest), "--models-dir", str(tmp_path / "m"), "autre") == 2
