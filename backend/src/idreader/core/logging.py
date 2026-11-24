"""Journal technique.

Règle (docs/05_SECURITE_RGPD.md § Journaux techniques) : horodatage, niveau, module,
identifiants (`job_id`, `document_id`…) et codes d'erreur uniquement. Jamais de nom,
prénom, numéro, MRZ, date de naissance ni nom de fichier d'origine.
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure le journal racine sur stderr (launchd redirige vers ~/Library/Logs/idreader/)."""
    root = logging.getLogger()
    if any(getattr(h, "_idreader", False) for h in root.handlers):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler._idreader = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def log_exception_safely(logger: logging.Logger, exc: BaseException, context: str) -> None:
    """Journalise une exception sans son message.

    Les messages d'exception peuvent contenir des données personnelles (paramètres SQL,
    valeurs Pydantic…) : on ne garde que le type et la pile (fichier, ligne, fonction).
    """
    frames = traceback.extract_tb(exc.__traceback__)[-5:]
    where = " < ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in reversed(frames))
    logger.error("%s exception=%s at=%s", context, type(exc).__name__, where)
