"""Version de macOS et dernier banc d'essai (docs/06_DEPLOIEMENT_MAC_MINI.md § Supervision).

L'OCR de Vision change avec le système : si la version de macOS diffère de celle du dernier
banc lancé sur macOS, l'interface affiche une alerte tant qu'un nouveau banc n'a pas été
lancé (`uv run idreader bench --set golden --compare last`).
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

from pydantic import BaseModel


class BenchInfo(BaseModel):
    file: str  # nom du rapport JSON (sans chemin)
    set: str
    git: str | None
    started_at: str
    macos: str | None


class MacosAlert(BaseModel):
    current: str | None  # None hors macOS (développement sous Windows ou Linux)
    last_bench: BenchInfo | None  # dernier banc lancé sur macOS
    changed: bool  # True : alerte à afficher
    message: str | None


def current_macos() -> str | None:
    return platform.mac_ver()[0] or None


def last_macos_bench(results_dir: Path) -> BenchInfo | None:
    """Rapport le plus récent dont l'environnement indique une version de macOS."""
    best: BenchInfo | None = None
    if not results_dir.is_dir():
        return None
    for path in results_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        env = data.get("environment") or {}
        macos = env.get("macos") if isinstance(env, dict) else None
        started = data.get("started_at")
        if not macos or not isinstance(started, str):
            continue
        info = BenchInfo(
            file=path.name,
            set=str(data.get("set", "?")),
            git=data.get("git"),
            started_at=started,
            macos=str(macos),
        )
        if best is None or info.started_at > best.started_at:
            best = info
    return best


def macos_alert(current: str | None, results_dir: Path) -> MacosAlert:
    bench = last_macos_bench(results_dir)
    if current is None:
        return MacosAlert(current=None, last_bench=bench, changed=False, message=None)
    if bench is None:
        return MacosAlert(
            current=current,
            last_bench=None,
            changed=True,
            message=(
                f"Aucun banc d'essai lancé sous macOS {current} : lancer le banc avant de "
                "faire confiance aux résultats."
            ),
        )
    if bench.macos != current:
        return MacosAlert(
            current=current,
            last_bench=bench,
            changed=True,
            message=(
                f"macOS est passé de {bench.macos} à {current} depuis le dernier banc d'essai "
                f"({bench.started_at[:10]}) : relancer le banc golden avant de reprendre."
            ),
        )
    return MacosAlert(current=current, last_bench=bench, changed=False, message=None)
