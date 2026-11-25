# Avancement du projet IDReader

> Mis à jour à chaque étape. Dernière mise à jour : 2026-11-25.
> Développement hybride : tout ce qui ne dépend ni d'Apple Vision ni de vraies pièces se fait sous Windows ; le reste attend le Mac mini.

## Où on en est

| Phase | État | Commentaire |
|---|---|---|
| 0 — Socle | fait sous Windows, à valider sur macOS | Helper Swift `idvision` écrit sans compilateur : à compiler et tester sur le Mac (liste dans `docs/DECISIONS.md`). |
| 1a — MRZ sans Vision + banc synthétique | fait | Les trois critères sont atteints (2026-11-25). |
| 1b — Vision, calibration, banc golden | à faire (Mac) | Demande macOS et de vraies pièces. |
| 2 — Zone visuelle (G, H, L) | fait sous Windows, à valider sur macOS | G (PP-OCRv6 medium det + rec, fusion), H (MRZ guidée, libellés, dates multilingues), L (confiance, statuts, raisons, alertes), documents UTO synthétiques, worker branché, `idreader analyze`, banc `documents`. Critères chiffrés : à mesurer avec Vision (deux moteurs) et sur vraies pièces. |
| 3 — Vérification humaine et exports | fait sous Windows, à valider sur macOS | Écran de vérification (zoom, cadres, alternatives, MRZ colorée, raccourcis), édition en ligne, audit avant/après, exports XLSX/CSV, réglages, comptes, statistiques, « Ajouter au jeu de test ». Les trois critères (15 s au clavier, audit visible, Excel) se vérifient à la main sur le Mac. |
| 4 — Recto/verso, doublons, sans MRZ | à faire | Sans l'étape I (codes-barres, via Vision). |
| 5 — Exploitation (`deploy/`) | à faire | Fichiers seulement, rien n'est exécuté sous Windows. |
| 6 — IA locale (option) | à faire (Mac) | Décision sur mesures. |
| 7 — Gabarits | à faire | Demande les statistiques réelles. |

## Derniers résultats du banc

| Banc | Date | Commit | Moteurs actifs | Résultat | Représentatif |
|---|---|---|---|---|---|
| synthétique (MRZ), 300 cas, graine 2026 | 2026-11-25 | `7859b22` | ppocr | MRZ entièrement correcte 80,0 % · sommes valides 92,7 % · 0 erreur auto-validée · p50 225 ms | **non** (Windows, sans Vision ni Tesseract) |
| documents UTO (A→L), 60 documents, graine 2026 | 2026-11-25 | `c46c8b2` | ppocr | détection 100 % · champs obligatoires tous justes 30,0 % · noms justes 71,7 % sans accents, 38,5 % avec · auto-validation 0 % (un seul moteur) · **0 erreur auto-validée** · p50 3,8 s par document | **non** |

Rapports : `bench/results/2026-11-24_7859b22_synthetic.md`, `bench/results/2026-11-25_c46c8b2_documents.md` (date UTC dans le nom). Ces chiffres servent de référence pour suivre le code ; aucun poids ni seuil n'est calibré dessus.

## Lecture des résultats de la phase 2 (non représentatifs)

- Sans Vision, chaque valeur n'a qu'un moteur de zone visuelle : sexe, nationalité et noms restent « non prouvés », d'où 0 % d'auto-validation. C'est voulu (règle 3).
- Qualité « poor » et pages A4 (cartes à 300 dpi, petits caractères) : 0 % de documents entièrement justes avec PP-OCR seul.
- Accents : PP-OCR perd parfois un tréma ou un accent (`Ï`→`I`). La MRZ ne peut pas le voir ; d'où la règle « accents prouvés par deux moteurs » (DECISIONS du 2026-11-25).

## Ce qui reste bloqué, et pourquoi

| Point | Raison | Débloqué par |
|---|---|---|
| Helper Swift `idvision` (compilation, tests, `doctor`) | Swift et Vision n'existent que sur macOS | Mac mini |
| Apple Vision dans le pipeline (MRZ, zone visuelle, détection, orientation, codes-barres) | idem | Mac mini, phase 1b |
| Calibration des poids du vote, des seuils de qualité et de confiance | Interdite sous Windows : banc non représentatif | Banc complet sur le Mac + golden |
| Banc `golden` et critères chiffrés sur vraies pièces | Aucune vraie pièce sur ce PC (règle du projet) | Mac mini, disque chiffré |
| Tesseract sous Windows | L'installation demande les droits administrateur | `winget install --id UB-Mannheim.TesseractOCR` (facultatif) ou `brew install tesseract` sur le Mac |
| Parcours navigateur (Safari, Chrome) | Le navigateur piloté n'a pas pu être connecté sous Windows | Test manuel |

## Quand j'arrive sur le Mac

Marche à suivre, du clone jusqu'au lancement de la phase 1b. Compter environ une heure.

### 1. Prérequis

```bash
sw_vers                                   # macOS 15 minimum (26+ conseillé)
sysctl hw.memsize                         # 8 ou 16 Go : à noter
xcode-select --install                    # compilateur Swift
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # si Homebrew absent
brew install tesseract caddy uv node git
corepack enable                           # pnpm
fdesetup status                           # FileVault doit être actif avant toute vraie pièce
```

### 2. Récupérer le projet

```bash
git clone https://github.com/Kyliantvv/new-scan-id.git idreader
cd idreader
```

Remplir `deploy/MACHINE.md` : sortie de `sw_vers`, mémoire, puce, disque libre, ressources de la VM Lima.

### 3. Backend

```bash
cd backend
uv sync
cp ../.env.example ../.env                # puis générer IDR_SECRET_KEY :
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
uv run alembic upgrade head
uv run idreader create-user --admin admin@exemple.fr
```

### 4. Modèles (téléchargement unique, empreintes vérifiées)

```bash
uv run python ../scripts/fetch_models.py           # télécharge ce que liste scripts/models.toml
uv run python ../scripts/fetch_models.py --verify  # doit afficher « ok » pour chaque fichier
```

### 5. Helper Swift

```bash
cd ..
swift build -c release --package-path vision-helper
swift test --package-path vision-helper
vision-helper/.build/release/idvision doctor        # version de macOS, révisions, langues Vision
```

### 6. Liste « À vérifier sur macOS »

Ouvrir `docs/DECISIONS.md`, section « À vérifier au premier lancement sur macOS », et cocher chaque point (helper Swift, backend, front, déploiement). Tout point qui échoue est corrigé avant la phase 1b.

### 7. Tests

```bash
cd backend
uv run pytest && uv run ruff check . && uv run mypy src   # les tests Vision réels ne sont plus « skipped »
cd .. && pnpm --dir frontend install && pnpm --dir frontend build && pnpm --dir frontend test
```

Parcours manuel dans Safari et Chrome : `uv run idreader serve`, `uv run idreader worker`, `pnpm --dir frontend dev`, puis connexion, lot, dépôt, progression.

### 8. Banc synthétique avec les trois moteurs

```bash
cd backend
uv run idreader bench --set synthetic --count 300 --seed 2026
```

Le rapport doit indiquer « Moteurs actifs : vision, tesseract, ppocr » et ne plus porter l'avertissement de non-représentativité. Comparer avec la référence Windows ci-dessus.

### 9. Lancer la phase 1b

Message à donner à Claude Code, ouvert dans le dépôt sur le Mac :

> Lis `CLAUDE.md`, `AVANCEMENT.md`, puis `docs/07_ROADMAP.md` (phase 1b) et les sections de `docs/03_PIPELINE_ANALYSE.md` qu'elle concerne. On est sur le Mac mini. Commence par la liste « À vérifier au premier lancement sur macOS » de `docs/DECISIONS.md` : compile le helper, fais passer ses tests, corrige ce qui échoue et coche la liste. Ensuite, propose un plan découpé en étapes vérifiables pour la phase 1b (intégration de Vision, calibration des poids du vote, banc synthétique complet, premier banc golden). Écris les tests avec le code. Un commit par étape, poussé aussitôt. Mets à jour `AVANCEMENT.md` à chaque étape. À la fin, donne-moi les critères d'acceptation avec leur état et les chiffres.
