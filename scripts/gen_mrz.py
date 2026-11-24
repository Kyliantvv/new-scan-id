#!/usr/bin/env python3
"""Génère le jeu synthétique du banc : MRZ valides (identités inventées), rendues en police
OCR-B sur des fonds variés, puis dégradées (flou, bruit, contraste, reflet, perspective,
rotation, compression JPEG).

    cd backend && uv run python ../scripts/gen_mrz.py --count 300 --seed 2026 --out ../data/bench/synthetic/demo

Police : `models/fonts/ocrb10.otf` (OCR-B de Norbert Schwarz, contours de Zdeněk Wagner,
CTAN fonts/ocr-b-outline ; « freely use, modify, and/or distribute … without limitation »),
installée par `scripts/fetch_models.py`. Voir docs/DECISIONS.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from idreader.bench.synthetic import QUALITIES, generate
from idreader.core.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quality", choices=QUALITIES, action="append", help="répétable ; défaut : toutes")
    args = parser.parse_args(argv)
    settings = get_settings()
    font = settings.models_dir / "fonts" / "ocrb10.otf"
    if not font.is_file():
        print("Police OCR-B absente : lancer scripts/fetch_models.py", file=sys.stderr)
        return 2
    cases = generate(args.out, count=args.count, seed=args.seed, font_path=font,
                     qualities=tuple(args.quality or QUALITIES))
    print(f"{len(cases)} cas écrits dans {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
