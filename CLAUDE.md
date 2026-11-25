# IDReader — lecture automatique de pièces d'identité

> Nom de travail, à renommer librement. Projet distinct de « Scan » (PHP/Node, CNI françaises) : ici, pièces du monde entier, stack choisie pour le taux de réussite.

## En une phrase

Site web interne qui reçoit des PDF, JPG et PNG de passeports et de cartes d'identité de tous pays, en extrait les données (nom, prénoms, dates, numéro, nationalité…) et les affiche dans un tableau éditable et exportable. **Tout tourne en local sur un Mac mini M1.**

## Règles non négociables

1. **100 % local.** Aucun appel réseau sortant à l'exécution : pas d'API cloud, pas de télémétrie, pas de CDN dans le front (polices et scripts servis localement). Les modèles sont téléchargés une seule fois par `scripts/fetch_models.py` avec vérification SHA-256, puis on force `HF_HUB_OFFLINE=1`.
2. **Aucune donnée personnelle dans les logs**, les messages d'erreur, les noms de fichiers stockés, les tests commités ou les tickets. On logge des identifiants (`document_id`, `job_id`), jamais un nom, un numéro ou une MRZ.
3. **Mieux vaut « À vérifier » qu'une erreur validée.** Un document n'est validé automatiquement que si ses valeurs sont *prouvées* (sommes de contrôle MRZ, concordance entre sources indépendantes). Une valeur produite uniquement par un modèle génératif n'est jamais validée automatiquement.
4. **Tout changement du moteur se mesure.** `uv run idreader bench` avant et après. On ne fusionne pas une régression (voir `docs/04_EVALUATION.md`).
5. **Le moteur tourne nativement sur macOS**, jamais dans Docker ni dans la VM Lima du Mac mini : Apple Vision, le Neural Engine et Metal ne sont pas accessibles depuis un conteneur ou une VM Linux.

## Stack (justification détaillée : `docs/02_ARCHITECTURE.md`)

| Rôle | Choix |
|---|---|
| API + worker | Python 3.12, `uv`, FastAPI, SQLAlchemy 2 + Alembic, SQLite (WAL), Pydantic v2, Typer (CLI) |
| OCR n°1 | Apple Vision via le binaire Swift `idvision` (`vision-helper/`) |
| OCR n°2 | RapidOCR (`rapidocr` + ONNX Runtime) avec modèles PP-OCRv6 (latin, chinois, japonais) et PP-OCRv5 (cyrillique, arabe, grec, coréen, thaï, devanagari) |
| OCR n°3 (MRZ seulement) | Tesseract 5 + `mrz.traineddata` |
| Image / PDF | OpenCV (`opencv-python-headless`), Pillow, `pypdfium2` ; HEIC décodé par ImageIO via `idvision` |
| Texte | `rapidfuzz`, `anyascii` + table de translittération ICAO, `iso3166` + `Babel` (pays) |
| IA locale (option, phase 6) | `mlx-vlm` : PaddleOCR-VL 1.6 (0,9 Md) et/ou Qwen3-VL-4B-Instruct en 4 bits |
| Front | React + Vite + TypeScript, AG Grid Community (tableau), Tailwind CSS |
| Service | launchd (LaunchAgents), Caddy (HTTPS sur le réseau local) |

## Arborescence cible

```
idreader/
├── CLAUDE.md
├── docs/                    # spécifications (lire avant de coder)
├── reference/               # données de référence YAML (libellés, mois, codes ICAO, écritures)
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── src/idreader/
│   │   ├── api/             # routes FastAPI
│   │   ├── core/            # config, db, modèles ORM, sécurité, audit
│   │   ├── engines/         # moteurs OCR (interface OcrEngine + implémentations)
│   │   ├── pipeline/        # étapes A→L du pipeline (fonctions pures)
│   │   │   └── mrz/         # localisation, lecture, vote, formats, sommes de contrôle, réparation
│   │   ├── worker/          # file de jobs, exécution
│   │   ├── exports/         # xlsx, csv
│   │   └── cli.py           # serve, worker, analyze, bench, purge, create-user
│   └── tests/
│       ├── unit/
│       └── fixtures/        # spécimens publics et images synthétiques UNIQUEMENT
├── vision-helper/           # package Swift « idvision »
├── frontend/
├── bench/                   # résultats du banc d'essai (JSON + rapports)
├── deploy/                  # plists launchd, Caddyfile, scripts d'installation
├── scripts/                 # fetch_models.py, génération de MRZ synthétiques…
├── models/                  # (gitignore) modèles téléchargés
└── data/                    # (gitignore) fichiers, recadrages, base SQLite, golden set
```

## Commandes

```bash
# Backend
cd backend && uv sync
uv run alembic upgrade head
uv run idreader create-user --admin admin@exemple.fr
uv run idreader serve                      # API sur 127.0.0.1:8000
uv run idreader worker                     # worker d'analyse
uv run idreader analyze chemin/fichier.pdf --debug   # pipeline en CLI, artefacts dans data/debug/
uv run idreader bench --set golden --compare last
uv run idreader diagnose --batch <id>       # où le pipeline casse sur un lot, sans donnée personnelle
uv run pytest && uv run ruff check . && uv run mypy src

# Helper Swift
swift build -c release --package-path vision-helper
vision-helper/.build/release/idvision doctor   # version de macOS, langues Vision disponibles

# Front
pnpm --dir frontend install && pnpm --dir frontend dev
pnpm --dir frontend build                      # build servi par FastAPI
```

## Conventions

- **Langue** : identifiants, noms de fichiers et de colonnes en anglais ; commentaires, docstrings, interface, messages et docs en français.
- **Qualité** : typage strict (`mypy --strict` sur `pipeline/` et `engines/`), `ruff`, un test unitaire par fonction du pipeline (sans image réelle).
- **Pipeline** : chaque étape est une fonction pure. Entrée = artefacts de l'étape précédente, sortie = modèle Pydantic sérialisable en JSON. Les artefacts sont mis en cache sur disque par étape, ce qui permet de relancer une seule étape.
- **Moteurs** : tout moteur OCR implémente `OcrEngine` (`engines/base.py`). Le pipeline ne connaît pas les moteurs concrets.
- **Coordonnées** : toujours en pixels, origine en haut à gauche, dans le repère de l'image recadrée. Vision renvoie des coordonnées normalisées avec l'origine en bas à gauche : la conversion se fait **uniquement** dans le helper Swift.
- **Dates** : ISO 8601 (`AAAA-MM-JJ`) en base et dans l'API, affichage `JJ/MM/AAAA`.
- **Dépendances** : licences acceptées : MIT, BSD, Apache-2.0, ISC, MIT-CMU, PSF. LGPL acceptée seulement en bibliothèque dynamique non modifiée, et documentée dans `docs/DECISIONS.md`. GPL et AGPL interdites. Toute nouvelle dépendance est ajoutée au tableau de `docs/02_ARCHITECTURE.md` avec sa licence.
- **Normes** : quand une règle vient d'ICAO Doc 9303, l'écrire en commentaire avec la référence (ex. `# ICAO 9303 partie 5, §4.2`).

## Comment travailler sur ce projet

- Suivre `docs/07_ROADMAP.md` phase par phase. Ne pas attaquer une phase avant d'avoir atteint les critères d'acceptation de la précédente.
- Avant de coder une étape du pipeline, relire sa section dans `docs/03_PIPELINE_ANALYSE.md`.
- Les images dans le dépôt sont **uniquement** des spécimens publics ou des images synthétiques (`backend/tests/fixtures/`). Les vraies pièces restent dans `data/golden/` (hors git, disque chiffré).
- Si une règle de ces documents semble fausse face aux données réelles, ne pas la contourner en silence : le signaler, proposer la correction et mettre à jour le document concerné.
- Chaque décision prise en cours de route est notée dans `docs/DECISIONS.md` (date, décision, raison).
- Développement hybride : le backend et le front se développent aussi sous Windows, mais le code vise macOS (`pathlib` partout, aucun code propre à Windows). Ce qui ne peut être vérifié que sur macOS est listé dans `docs/DECISIONS.md`.

## Documents

| Fichier | Contenu |
|---|---|
| `docs/01_SPEC_FONCTIONNELLE.md` | Ce que fait le site : lots, tableau, statuts, vérification, exports |
| `docs/02_ARCHITECTURE.md` | Stack, composants, modèle de données, file de jobs, helper Swift, API |
| `docs/03_PIPELINE_ANALYSE.md` | Le moteur d'analyse, étape par étape (MRZ, zone visuelle, score) |
| `docs/04_EVALUATION.md` | Mesure du taux de réussite, banc d'essai, objectifs |
| `docs/05_SECURITE_RGPD.md` | Protection des données, accès, conservation |
| `docs/06_DEPLOIEMENT_MAC_MINI.md` | Installation et exploitation sur le Mac mini M1 |
| `docs/07_ROADMAP.md` | Phases, tâches et critères d'acceptation |
| `docs/DECISIONS.md` | Journal des décisions et points à vérifier sur macOS |
| `reference/*.yaml` | Libellés multilingues, mois, codes ICAO spéciaux, langues par pays |
