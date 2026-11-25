# 02 — Architecture technique

## Vue d'ensemble

```
  Navigateurs (réseau local)
          │ HTTPS
   ┌──────▼──────┐
   │    Caddy    │  TLS interne, en-têtes de sécurité, taille max d'upload
   └──────┬──────┘
          │ http://127.0.0.1:8000
   ┌──────▼───────────────────┐   table jobs   ┌──────────────────────────────────────┐
   │ API FastAPI              │◄──────────────►│ Worker Python (1 processus)          │
   │ · auth, lots, uploads    │    SQLite WAL  │ pipeline A→L                         │
   │ · tableau, vérification  │                │  ├─ OpenCV, pypdfium2, Pillow        │
   │ · exports, admin         │                │  ├─ RapidOCR (PP-OCRv6 / v5, ONNX)   │
   │ · SSE de progression     │                │  ├─ Tesseract 5 (mrz.traineddata)    │
   │ · sert le front (build)  │                │  ├─ idvision (Swift, Apple Vision) ◄─┼─ JSON lines stdin/stdout
   └──────────┬───────────────┘                │  └─ [option] mlx-vlm                 │
              │                                └──────────────────┬───────────────────┘
              └────────────────────┬──────────────────────────────┘
                                   ▼
             data/  (originaux, pages, recadrages, artefacts, base SQLite)
             sur un disque chiffré (FileVault)
```

Tout tourne **sur macOS directement** (pas de Docker, pas de VM) : c'est la condition pour utiliser Apple Vision sur le Neural Engine.

## Pourquoi ces choix

| Choix | Raison |
|---|---|
| **Python** pour l'API et le worker | Tout l'écosystème vision/OCR est en Python (OpenCV, ONNX Runtime, PaddleOCR, MLX). Un seul langage côté serveur. |
| **Apple Vision** via un binaire Swift | OCR intégré à macOS, exécuté sur le Neural Engine du M1 : rapide, gratuit, déjà installé, très bon sur le latin, le cyrillique, le chinois, le japonais, le coréen, l'arabe, le thaï, le vietnamien (la liste exacte dépend de la version de macOS, d'où `idvision languages`). Les API récentes (`RecognizeTextRequest` depuis macOS 15, `RecognizeDocumentsRequest` depuis macOS 26) n'existent qu'en Swift. |
| **PP-OCRv6 / v5** via RapidOCR | Deuxième moteur **indépendant** d'Apple, léger (de 1,5 M à 34,5 M de paramètres pour PP-OCRv6), sur CPU via ONNX Runtime. Croiser deux moteurs indépendants est ce qui fait le plus monter le taux. PP-OCRv6 couvre 50 langues (chinois, anglais, japonais et 46 langues latines) ; l'arabe, le cyrillique, le grec, le coréen, le thaï et le devanagari passent par les modèles PP-OCRv5 dédiés. |
| **Tesseract + mrz.traineddata** | Troisième lecture, spécialisée dans la police OCR-B de la MRZ. Rapide et différente des deux autres, donc utile pour le vote. |
| **SQLite** | Un seul serveur, volume modeste (quelques milliers de documents par mois). Mode WAL pour les lectures concurrentes. La file de jobs est une table : pas de Redis à maintenir. |
| **React + AG Grid Community** | Tableau riche (tri, filtres, édition en ligne, sélection, export CSV) sans tout recoder. Licence MIT. |
| **IA générative locale optionnelle** | Un petit modèle de vision (PaddleOCR-VL 0,9 Md ou Qwen3-VL-4B en 4 bits) tourne sur un M1, mais en dizaines de secondes par image. On le réserve aux cas difficiles, derrière un interrupteur, et après mesure sur le banc d'essai (phase 6). |

## Modules Python (`backend/src/idreader/`)

```
core/
  config.py        # Settings Pydantic (lecture .env), chemins, seuils
  db.py            # moteur SQLAlchemy, PRAGMA (WAL, foreign_keys, busy_timeout)
  models.py        # ORM (voir modèle de données)
  security.py      # argon2id, sessions, CSRF, rôles
  audit.py         # journal d'audit
engines/
  base.py          # Protocol OcrEngine, TextLine, Quad, Candidate
  apple_vision.py  # client du processus idvision (JSON lines)
  ppocr.py         # PP-OCR (ONNX Runtime) ; phase 1a : reconnaissance seule, ligne par ligne (MRZ)
  tesseract.py     # Tesseract MRZ (sous-processus, PNG sur stdin, sortie TSV)
  registry.py      # moteurs disponibles sur la machine (n'importe quel sous-ensemble)
  vlm.py           # [option] mlx-vlm, chargement paresseux, déchargement après inactivité
pipeline/
  ingest.py        # A — PDF → pages, images → RGB, EXIF, HEIC (via idvision), empreinte
  quality.py       # B — flou, reflet, résolution estimée
  detect.py        # C — détection et recadrage des documents
  orient.py        # D — orientation 0/90/180/270 + redressement fin
  mrz/
    locate.py      # E.1 — localisation de la bande MRZ (repli morphologique), lignes, redressement fin
    read.py        # E.2 — lectures multi-moteurs × prétraitements
    normalize.py   # E.2 — normalisation d'une lecture (chevrons, accents, K de remplissage, réalignement)
    vote.py        # E.3 — alignement et vote par caractère
    formats.py     # E.4 — TD1, TD2, TD3, MRV-A, MRV-B, FRA ancienne CNI
    checkdigit.py  # E.5 — sommes de contrôle et classes indiscernables
    repair.py      # E.6 — réparation guidée par les sommes de contrôle
    names.py       # E.7 — noms, troncature, translittération ICAO
    decode.py      # E.4→E.7 assemblés : champs, statuts, preuves, contrôles sémantiques
    synth.py       # MRZ synthétiques valides (tests, scripts/gen_mrz.py)
    stage.py       # étape E de bout en bout sur un recadrage (artefact JSON)
  reference.py     # chargement de reference/*.yaml
  classify.py      # F — type de document et pays
  viz_ocr.py       # G — OCR de la zone visuelle, fusion des moteurs
  extract.py       # H — extraction des champs (guidée par la MRZ, libellés, gabarits)
  dates.py         # H — analyse de dates multilingue
  barcode.py       # I — codes-barres
  pairing.py       # J — appariement recto/verso
  vlm_fallback.py  # K — [option] IA locale avec règle d'ancrage
  consolidate.py   # L — fusion des preuves, confiance, statut
bench/
  synthetic.py     # jeu synthétique : rendu OCR-B, dégradations, vérité (truth.json)
  runner.py        # `idreader bench` : exécution, indicateurs, rapports JSON + Markdown
  runner.py        # enchaînement des étapes, cache des artefacts
worker/
  queue.py         # prise de job, reprise, priorités
  main.py          # boucle du worker
api/
  auth.py, batches.py, files.py, documents.py, exports.py, admin.py, events.py (SSE)
exports/
  xlsx.py, csv.py
cli.py             # Typer
```

## Modèle de données (SQLite)

```
users        id, email, password_hash, role (operator|admin), active, failed_logins, locked_until,
             created_at, last_login_at
sessions     id, token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at
batches      id, name, created_by, created_at, status, counts_json
files        id, batch_id, original_name, mime, sha256, size_bytes, storage_path, upload_order, created_at
pages        id, file_id, page_index, storage_path, width, height, quality_json
crops        id, page_id, quad_json, storage_path, width, height, rotation, side (front|back|unknown),
             physical_format (ID1|ID2|ID3|unknown), has_mrz, quality_json
documents    id, batch_id, status, status_reasons_json, doc_type, doc_code, issuing_state,
             mrz_format, mrz_lines_json, mrz_valid, confidence, alerts_json,
             front_crop_id, back_crop_id, duplicate_of_id,
             reviewed_by, reviewed_at, created_at, updated_at
fields       id, document_id, name, value, value_native, confidence, status (ok|repaired|conflict|missing|manual),
             evidence_json, candidates_json, bbox_json (crop_id + quad)
jobs         id, type, ref_id, batch_id, status (queued|running|done|failed), priority, attempts, progress, stage,
             error_code, worker_id, locked_at, heartbeat_at, run_after, created_at, finished_at
audit_log    id, user_id, action, entity, entity_id, before_json, after_json, at
settings     key, value_json
```

- `fields` en lignes (et non en colonnes de `documents`) : ajouter un champ ne demande pas de migration, et chaque champ garde ses preuves. Une vue SQL `documents_flat` à plat sert le tableau et les exports.
- `evidence_json` : liste des preuves, ex. `[{"src":"mrz","check":"ok"}, {"src":"viz","engine":"vision","text":"…","sim":0.97,"quad":[…]}]`.
- `error_code` des jobs : codes stables (`NO_DOCUMENT`, `TOO_BLURRY`, `GLARE_ON_MRZ`…), jamais de texte contenant des données.

## Stockage des fichiers

```
data/
  idreader.sqlite3
  batches/<batch_uuid>/
    files/<file_uuid>/original.<ext>
    pages/<page_uuid>.png
    crops/<crop_uuid>.png              # recadré, redressé, normalisé
    artifacts/<crop_uuid>/<stage>.json # sorties brutes des étapes (debug, rejouables)
  golden/                              # jeu de test réel (hors git)
  debug/                               # sorties de `idreader analyze --debug`
```

Noms internes = UUID. Le nom d'origine n'est stocké qu'en base.

## File de jobs

- Prise d'un job : transaction `BEGIN IMMEDIATE`, sélection du plus prioritaire `queued` avec `run_after <= now`, passage en `running` avec `locked_at`, `COMMIT`.
- Types : `process_file` (pipeline A→I pour toutes les pages d'un fichier), `pair_batch` (étape J puis K et L ; lancé quand tous les jobs du lot sont terminés et qu'aucun fichier n'a été ajouté depuis 2 minutes, ou par le bouton « Lancer l'appariement » ; relancé si de nouveaux fichiers arrivent), `reprocess_document`, `purge`.
- Battement de cœur : le worker qui exécute un job met à jour `heartbeat_at` toutes les 10 s ; `worker_id` identifie le processus.
- Reprise : au démarrage du worker et avant chaque prise de job, un job `running` sans battement depuis plus de 60 s est considéré abandonné et repasse en `queued` (`attempts + 1`, 3 essais maximum, puis `failed`).
- Progression : le worker met à jour `progress` et `stage` ; l'API interroge la table `jobs` toutes les secondes et diffuse les changements en SSE (`/api/events?batch=…`). Pas de bus de messages entre les deux processus.
- Concurrence : 1 worker par défaut (`IDR_WORKERS`). Le Neural Engine et le CPU du M1 sont partagés : on mesure avant d'augmenter.

## Helper Swift `idvision`

Package Swift dans `vision-helper/`, cible macOS 15 minimum, utilise les API de macOS 26 quand elles sont disponibles (`if #available(macOS 26, *)`).

**Mode `serve`** : processus long lancé par le worker, protocole JSON lines (une requête par ligne sur stdin, une réponse par ligne sur stdout, logs sur stderr sans données personnelles). Le processus garde les modèles Vision en mémoire entre les requêtes.

```jsonc
// Requêtes
{"id":"1","op":"version"}
{"id":"2","op":"languages"}                       // langues par niveau (fast/accurate)
{"id":"3","op":"detect_documents","image":"/abs/page.png","max":6}
{"id":"4","op":"recognize_text","image":"/abs/crop.png",
 "roi":[x,y,w,h],                                 // pixels, optionnel
 "level":"accurate","languages":["fr-FR","en-US"],
 "language_correction":false,"top_candidates":5,"min_text_height":0.0}
{"id":"5","op":"detect_barcodes","image":"/abs/crop.png"}
{"id":"6","op":"detect_faces","image":"/abs/crop.png"}
{"id":"7","op":"recognize_document","image":"/abs/crop.png"}   // macOS 26+ seulement
{"id":"8","op":"decode_image","image":"/abs/photo.heic","output":"/abs/photo.png"} // phase 1 : HEIC via ImageIO

// Réponse type (recognize_text)
{"id":"4","ok":true,"ms":412,"lines":[
  {"text":"P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<","confidence":0.5,
   "quad":[[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
   "candidates":[{"text":"…","confidence":0.5}, …]}]}

// Erreur
{"id":"4","ok":false,"error":"IMAGE_NOT_FOUND"}
```

- `detect_documents` combine la segmentation de document de Vision et la détection de rectangles (plusieurs résultats, rapport largeur/hauteur entre 1,3 et 1,7 pour les formats ID-1 à ID-3), et renvoie des quadrilatères en pixels avec une confiance.
- Coordonnées toujours converties en pixels, origine en haut à gauche.
- Mode ligne de commande pour le debug : `idvision recognize-text image.png --languages fr-FR,en-US`.
- `idvision doctor` : version de macOS, révisions des requêtes, langues disponibles par niveau. Appelé au démarrage du worker et affiché dans la page Santé.

## API REST (préfixe `/api`)

| Méthode | Chemin | Rôle |
|---|---|---|
| POST | `/auth/login`, `/auth/logout` · GET `/me` | session |
| GET/POST | `/batches` · GET `/batches/{id}` | lots |
| POST | `/batches/{id}/files` | upload multipart (plusieurs fichiers) |
| GET | `/documents?batch=&status=&q=&sort=&offset=&limit=` | tableau (pagination serveur pour la vue globale) |
| GET | `/documents/{id}` | détail : champs, preuves, alternatives, recadrages, cadres |
| PATCH | `/documents/{id}/fields` | corrections (journalisées) |
| POST | `/documents/{id}/validate` · `/reject` · `/reprocess` · `/golden` (admin) · DELETE `/documents/{id}` | actions |
| POST | `/documents/pair` · `/documents/{id}/unpair` | recto/verso |
| GET | `/crops/{id}/image` · `/pages/{id}/image` · `/files/{id}/original` | images (authentifiées, `Cache-Control: no-store`) |
| POST | `/exports` (corps JSON : `format`, `batch`, `status`, `ids`, `columns`, `include_status`) | export |
| GET | `/events?batch=` | SSE : progression des jobs |
| GET/PUT | `/admin/settings` · CRUD `/admin/users` · GET `/admin/stats` · `/admin/health` · POST `/admin/purge` | administration |

Le front est servi par FastAPI (fichiers du build Vite) sur la même origine : pas de CORS.

## Front (`frontend/`)

- React + Vite + TypeScript, Tailwind, TanStack Query pour les appels API.
- Tableau : AG Grid Community. Modèle client par lot (quelques milliers de lignes) ; vue globale paginée côté serveur avec l'Infinite Row Model (disponible en Community). Export Excel fait côté serveur (l'export Excel d'AG Grid est payant).
- Écran de vérification : image dans un conteneur zoomable écrit pour le projet (`ZoomImage.tsx` : molette, glisser, quart de tour), cadres en SVG superposé, formulaire à droite, raccourcis clavier.
- Aucune ressource externe (polices, icônes, scripts) : tout est empaqueté dans le build.

## Configuration (`.env`)

```
IDR_DATA_DIR=/Users/idreader/idreader-data
IDR_SECRET_KEY=…                 # généré à l'installation
IDR_BIND=127.0.0.1:8000
IDR_WORKERS=1
IDR_RETENTION_IMAGES_DAYS=30
IDR_RETENTION_DATA_DAYS=365
IDR_AUTO_VALIDATE_THRESHOLD=0.95
IDR_COOKIE_SECURE=true          # false seulement en test / développement local en http
IDR_VLM_ENABLED=false
IDR_VLM_MODEL=mlx-community/PaddleOCR-VL-1.6-4bit
IDR_TESSDATA_PREFIX=/opt/homebrew/share/tessdata
HF_HUB_OFFLINE=1
```

## Dépendances et licences

| Dépendance | Usage | Licence |
|---|---|---|
| FastAPI, Uvicorn, Pydantic | API | MIT / BSD |
| SQLAlchemy, Alembic | base de données | MIT |
| Typer | CLI | MIT |
| httpx | client de test FastAPI (développement) | BSD-3 |
| pytest, ruff, mypy | tests et qualité (développement) | MIT |
| pydantic-settings | configuration (.env) | MIT |
| python-multipart | upload multipart (FastAPI) | Apache-2.0 |
| PyYAML (+ types-PyYAML en développement) | lecture de `reference/*.yaml` | MIT / Apache-2.0 |
| NumPy | tableaux d'images | BSD-3 (parties 0BSD, MIT, Zlib, CC0 : permissives) |
| argon2-cffi | mots de passe | MIT |
| opencv-python-headless | traitement d'image | Apache-2.0 (embarque FFmpeg en LGPL, bibliothèque dynamique non modifiée : exception acceptée, voir `DECISIONS.md`) |
| Pillow | images (en phase 0 : images synthétiques des tests seulement) | MIT-CMU |
| pypdfium2 | rendu PDF | Apache-2.0 / BSD |
| onnxruntime (+ flatbuffers, protobuf, packaging) | inférence des modèles PP-OCR (phase 1a : appel direct, sans RapidOCR) | MIT (Apache-2.0, BSD-3, Apache-2.0/BSD) |
| rapidocr | non utilisé à ce stade : la zone visuelle appelle aussi PP-OCRv6 directement par onnxruntime (détection DB + reconnaissance, `engines/ppocr.py`) | Apache-2.0 |
| Modèles PP-OCRv6 / v5 | OCR (MRZ : small rec 21 Mo ; zone visuelle : medium det 62 Mo + medium rec 77 Mo) | Apache-2.0 |
| Tesseract 5 | OCR MRZ | Apache-2.0 |
| mrz.traineddata (DoubangoTelecom/tesseractMRZ) | modèle MRZ | BSD-3-Clause (vérifié le 2026-11-24, fichier LICENSE du dépôt) |
| Police OCR-B `ocrb10.otf` (CTAN fonts/ocr-b-outline) | rendu des MRZ synthétiques (banc, tests) ; jamais servie au front | libre sans limitation (« freely use, modify, and/or distribute »), CTAN other-free |
| Police Roboto (google/fonts, `apache/roboto`) | rendu de la zone visuelle des documents synthétiques UTO (banc, tests) ; jamais servie au front | Apache-2.0 |
| rapidfuzz | similarité de chaînes | MIT |
| anyascii | translittération | ISC |
| iso3166 | codes pays alpha-2 / alpha-3 | MIT |
| Babel | noms de pays en français, formats de dates | BSD-3 |
| openpyxl (+ et_xmlfile) | export XLSX | MIT (MIT) |
| mlx-vlm (option) | IA locale | MIT |
| PaddleOCR-VL 1.6, Qwen3-VL-4B (option) | IA locale | Apache-2.0 |
| React, React Router, Vite, Tailwind, TanStack Query | front | MIT |
| TypeScript | front (typage, développement) | Apache-2.0 |
| Vitest | tests du front (développement) | MIT |
| AG Grid Community, ag-grid-react, @ag-grid-community/locale | tableau, traduction française | MIT |
| react-zoom-pan-pinch | zoom | MIT |
| Caddy | reverse proxy TLS | Apache-2.0 |

> Vérifier chaque licence au moment de l'ajout (elles peuvent changer d'une version à l'autre) et mettre ce tableau à jour. Licences acceptées : MIT, BSD, Apache-2.0, ISC, MIT-CMU, PSF. LGPL seulement en bibliothèque dynamique non modifiée et documentée. GPL et AGPL interdites (d'où l'absence de `pillow-heif`, dont les wheels embarquent libheif, libde265 et x265). Le générateur de MRZ de test est écrit maison dans `scripts/` (la somme de contrôle tient en dix lignes).
