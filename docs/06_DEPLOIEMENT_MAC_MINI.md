# 06 — Déploiement sur le Mac mini M1

## À relever avant de commencer (dans `deploy/MACHINE.md`)

- Version exacte de macOS (`sw_vers`).
- Mémoire vive : 8 ou 16 Go (`sysctl hw.memsize`). Elle conditionne la phase 6 (IA locale).
- Espace disque libre.
- Ressources déjà prises par la VM Lima de préprod (mémoire et CPU alloués).

## Prérequis

- macOS 15 minimum ; macOS 26 ou plus récent pour profiter de `RecognizeDocumentsRequest`.
- Xcode Command Line Tools (`xcode-select --install`) pour compiler le helper Swift.
- Homebrew : `brew install tesseract caddy uv node`, puis `corepack enable` pour pnpm.
- `mrz.traineddata` copié dans le dossier `tessdata` de Homebrew (chemin renvoyé par `brew --prefix tesseract`, sous `share/tessdata`).
- Modèles PP-OCR (et, en option, modèles MLX) téléchargés par `scripts/fetch_models.py`.

## Natif sur macOS, à côté de la VM de préprod

- L'application (API, worker, helper Swift) tourne **directement sur macOS**, pas dans la VM Lima ni dans Docker : Vision, le Neural Engine et Metal n'y sont pas accessibles.
- Limiter la mémoire de la VM Lima pour laisser de la marge au worker. Surveiller `memory_pressure` pendant un gros lot.
- Le service ne passe **pas** par Cloudflare Tunnel / Dokploy (voir `05_SECURITE_RGPD.md`).

## Compte et services

- Compte macOS dédié `idreader` (standard, non admin), propriétaire de `IDR_DATA_DIR`.
- Services **launchd** dans `deploy/launchd/` :
  - `fr.idreader.api.plist` → `uv run idreader serve`
  - `fr.idreader.worker.plist` → `uv run idreader worker` (lance lui-même `idvision serve`)
  - `fr.idreader.caddy.plist` → `caddy run --config deploy/Caddyfile`
  - `KeepAlive`, `RunAtLoad`, journaux dans `~/Library/Logs/idreader/`.
- Les installer comme **LaunchAgents** de la session `idreader` : Vision fonctionne de façon sûre dans une session utilisateur ouverte. Tester en LaunchDaemon (sans session) avant de s'y fier.

## Énergie et redémarrage

```bash
sudo pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 womp 1
```

- **FileVault** chiffre le disque mais, après une coupure de courant, le Mac attend le mot de passe au démarrage : prévoir qui le saisit (procédure écrite dans `deploy/MACHINE.md`).
- Pour un redémarrage planifié sans intervention : `sudo fdesetup authrestart`.

## Mises à jour de macOS

- Désactiver l'installation automatique des mises à jour de macOS sur cette machine.
- Avant une mise à jour : sauvegarde complète (Time Machine ou clone).
- Après une mise à jour, **avant** de relancer le service pour les utilisateurs : `idvision doctor` puis `idreader bench --set golden --compare last`. Raison : l'OCR de Vision change avec le système, et des régressions ont été signalées par des développeurs sur iOS 27 (lignes ignorées en mode `accurate`) et avec Vision 26.7 (langue thaïe plus reconnue, plantage en mode `accurate` dans une app iOS sur Mac).
- Si le banc régresse : rester sur la version précédente (restauration) ou augmenter le poids des moteurs non Apple en attendant, mesures à l'appui.

## Installation (script `deploy/install.sh`, idempotent)

1. Vérifier les prérequis (versions, espace disque, RAM) et afficher ce qui manque.
2. Créer `IDR_DATA_DIR` avec les droits `700`.
3. `uv sync --frozen` ; `alembic upgrade head`.
4. `swift build -c release --package-path vision-helper` ; `idvision doctor`.
5. `pnpm --dir frontend install --frozen-lockfile && pnpm --dir frontend build`.
6. `scripts/fetch_models.py --verify`.
7. Générer `IDR_SECRET_KEY` si absent ; créer le premier admin.
8. Installer les plists launchd ; démarrer ; vérifier `/api/admin/health`.

## Sauvegardes

- restic chaque nuit vers un disque externe chiffré (`deploy/backup.sh`, LaunchAgent `fr.idreader.backup` à 2 h 30, après la purge de 2 h 00) : base SQLite (via `sqlite3 .backup`, jamais une copie à chaud du fichier), `data/batches/`, `data/golden/`, `.env`.
- Rétention alignée sur `05_SECURITE_RGPD.md` : chaque famille d'instantanés est oubliée à sa durée de conservation + 7 jours.
- Mise en place, restauration et test trimestriel : `deploy/BACKUP.md`.

## Purge

- `uv run idreader purge` (LaunchAgent `fr.idreader.purge`, chaque jour à 2 h 00) : images et artefacts au-delà de `retention_images_days`, lots et journal au-delà de `retention_data_days`, dossiers de débogage, sessions expirées, puis `VACUUM`. `--dry-run` pour compter sans supprimer, `--batch <id>` pour un lot entier.
- Aussi depuis l'interface : Administration > Purge (simulation ou purge immédiate) et « Purger le lot » sur la page d'un lot (administrateurs).

## Supervision

- Page Santé (`/admin/health`) : version de macOS, langues Vision, révisions des requêtes, modèles chargés, jobs en attente et en échec, espace disque, mémoire, date du dernier banc d'essai.
- Alerte visible dans l'interface (bandeau pour les administrateurs, page Santé) si la version de macOS diffère de celle du dernier banc d'essai lancé sous macOS (`bench/results/*.json`), ou si aucun banc n'a été lancé sur cette version.
