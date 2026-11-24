#!/usr/bin/env python3
"""Téléchargement unique et vérifié des modèles (CLAUDE.md, règle 1 ; docs/05_SECURITE_RGPD.md).

Le manifeste versionné `scripts/models.toml` liste chaque modèle : URL, empreinte SHA-256,
licence, chemin de destination sous `models/`. Après téléchargement, `models/MANIFEST.json`
récapitule ce qui est installé. À l'exécution, l'application ne télécharge rien
(`HF_HUB_OFFLINE=1`).

Utilisation :
    python scripts/fetch_models.py              # télécharge ce qui manque, vérifie tout
    python scripts/fetch_models.py --verify     # vérifie sans rien télécharger (écart = échec)
    python scripts/fetch_models.py --pin [NOM]  # télécharge et AFFICHE l'empreinte, à relire
                                                # puis à reporter et commiter dans models.toml

Bibliothèque standard uniquement. Schémas acceptés : https:// et file:// (tests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "scripts" / "models.toml"
DEFAULT_MODELS_DIR = REPO_ROOT / "models"
ALLOWED_SCHEMES = ("https", "file")
CHUNK = 1024 * 1024
TIMEOUT_SECONDS = 60
USER_AGENT = "idreader-fetch-models"


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    sha256: str
    license: str
    dest: str

    def path(self, models_dir: Path) -> Path:
        return models_dir / PurePosixPath(self.dest)


def load_manifest(path: Path) -> list[ModelSpec]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    specs: list[ModelSpec] = []
    names: set[str] = set()
    for raw in data.get("model", []):
        try:
            spec = ModelSpec(
                name=str(raw["name"]),
                url=str(raw["url"]),
                sha256=str(raw.get("sha256", "")).lower(),
                license=str(raw["license"]),
                dest=str(raw["dest"]),
            )
        except KeyError as exc:
            raise FetchError(f"manifeste : champ manquant {exc} ") from None
        if spec.name in names:
            raise FetchError(f"manifeste : nom en double « {spec.name} »")
        names.add(spec.name)
        dest = PurePosixPath(spec.dest)
        if dest.is_absolute() or ".." in dest.parts or not dest.parts:
            raise FetchError(f"{spec.name} : destination invalide")
        scheme = urllib.parse.urlparse(spec.url).scheme
        if scheme not in ALLOWED_SCHEMES:
            raise FetchError(f"{spec.name} : schéma d'URL refusé ({scheme or 'aucun'})")
        if spec.sha256 and (
            len(spec.sha256) != 64 or not all(c in "0123456789abcdef" for c in spec.sha256)
        ):
            raise FetchError(f"{spec.name} : empreinte SHA-256 mal formée")
        specs.append(spec)
    return specs


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> tuple[str, int]:
    """Télécharge `url` dans `dest`. Renvoie (sha256, taille)."""
    if urllib.parse.urlparse(url).scheme not in ALLOWED_SCHEMES:
        raise FetchError("schéma d'URL refusé")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    h = hashlib.sha256()
    size = 0
    with (
        # Schéma contrôlé ci-dessus : https:// en production, file:// pour les tests.
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response,  # noqa: S310
        dest.open("wb") as out,
    ):
        while chunk := response.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return h.hexdigest(), size


def fetch(specs: list[ModelSpec], models_dir: Path, out: TextIO = sys.stdout) -> int:
    errors = 0
    models_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        target = spec.path(models_dir)
        if not spec.sha256:
            print(
                f"ERREUR {spec.name} : empreinte non épinglée (lancer --pin, relire, commiter)",
                file=out,
            )
            errors += 1
            continue
        if target.is_file() and sha256_file(target) == spec.sha256:
            print(f"ok     {spec.name} : déjà présent", file=out)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=models_dir) as tmp:
            part = Path(tmp) / "download.part"
            try:
                digest, size = download(spec.url, part)
            except (OSError, FetchError) as exc:
                print(
                    f"ERREUR {spec.name} : téléchargement impossible ({type(exc).__name__})",
                    file=out,
                )
                errors += 1
                continue
            if digest != spec.sha256:
                print(
                    f"ERREUR {spec.name} : empreinte différente du manifeste, fichier rejeté",
                    file=out,
                )
                errors += 1
                continue
            shutil.move(part, target)
        print(f"ok     {spec.name} : téléchargé ({size} octets)", file=out)
    write_installed_manifest(specs, models_dir)
    return errors


def verify(specs: list[ModelSpec], models_dir: Path, out: TextIO = sys.stdout) -> int:
    errors = 0
    for spec in specs:
        target = spec.path(models_dir)
        if not spec.sha256:
            print(f"ERREUR {spec.name} : empreinte non épinglée", file=out)
            errors += 1
        elif not target.is_file():
            print(f"ERREUR {spec.name} : absent", file=out)
            errors += 1
        elif sha256_file(target) != spec.sha256:
            print(f"ERREUR {spec.name} : empreinte différente du manifeste", file=out)
            errors += 1
        else:
            print(f"ok     {spec.name}", file=out)
    return errors


def pin(specs: list[ModelSpec], models_dir: Path, out: TextIO = sys.stdout) -> int:
    errors = 0
    models_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        with tempfile.TemporaryDirectory(dir=models_dir) as tmp:
            try:
                digest, size = download(spec.url, Path(tmp) / "pin.part")
            except (OSError, FetchError) as exc:
                print(
                    f"ERREUR {spec.name} : téléchargement impossible ({type(exc).__name__})",
                    file=out,
                )
                errors += 1
                continue
        status = "inchangée" if digest == spec.sha256 else "À REPORTER dans models.toml"
        print(f"{spec.name} : {size} octets", file=out)
        print(f'  sha256 = "{digest}"   # {status}', file=out)
    return errors


def write_installed_manifest(specs: list[ModelSpec], models_dir: Path) -> Path:
    entries = []
    for spec in specs:
        target = spec.path(models_dir)
        if target.is_file() and spec.sha256 and sha256_file(target) == spec.sha256:
            entries.append(
                {
                    "name": spec.name,
                    "file": spec.dest,
                    "sha256": spec.sha256,
                    "size": target.stat().st_size,
                    "license": spec.license,
                    "url": spec.url,
                }
            )
    path = models_dir / "MANIFEST.json"
    payload = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "models": entries}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Modèles d'IDReader : téléchargement vérifié.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="vérifier sans télécharger")
    mode.add_argument("--pin", action="store_true", help="télécharger et afficher les empreintes")
    parser.add_argument("names", nargs="*", help="limiter à ces modèles")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    args = parser.parse_args(argv)

    try:
        specs = load_manifest(args.manifest)
    except (OSError, tomllib.TOMLDecodeError, FetchError) as exc:
        print(f"ERREUR manifeste : {exc}", file=sys.stderr)
        return 2
    if args.names:
        unknown = set(args.names) - {s.name for s in specs}
        if unknown:
            print(f"ERREUR modèles inconnus : {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        specs = [s for s in specs if s.name in args.names]
    if not specs:
        print("Aucun modèle dans le manifeste.")
        if not args.verify and not args.pin:
            write_installed_manifest([], args.models_dir)
        return 0
    if args.verify:
        errors = verify(specs, args.models_dir)
    elif args.pin:
        errors = pin(specs, args.models_dir)
    else:
        errors = fetch(specs, args.models_dir)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
