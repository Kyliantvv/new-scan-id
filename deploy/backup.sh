#!/usr/bin/env bash
# Sauvegarde nocturne d'IDReader avec restic (docs/06_DEPLOIEMENT_MAC_MINI.md § Sauvegardes,
# procédure complète : deploy/BACKUP.md). Lancé chaque nuit par fr.idreader.backup (launchd),
# après la purge de 2 h 00 : une sauvegarde ne contient jamais ce que la purge a supprimé.
#
#   deploy/backup.sh           sauvegarde, puis oubli des instantanés trop anciens
#   deploy/backup.sh --init    crée le dépôt restic (une seule fois)
#
# Configuration : ~/.config/idreader/backup.env (droits 600, hors du dépôt git) :
#   RESTIC_REPOSITORY=/Volumes/IDReader-Sauvegarde/restic    # disque externe chiffré
#   RESTIC_PASSWORD_FILE=$HOME/.config/idreader/restic-password
#   GOLDEN_KEEP_DAYS=372                                      # facultatif (décision du DPO)
#
# Trois familles d'instantanés, chacune oubliée à sa durée de conservation + 7 jours
# (docs/05_SECURITE_RGPD.md) :
#   idreader-data    base SQLite (copie cohérente par `sqlite3 .backup`) et .env
#   idreader-images  data/batches/ (originaux, pages, recadrages, artefacts)
#   idreader-golden  data/golden/ (jeu de test réel)
# Aucun appel réseau : le dépôt est sur un disque local. Aucune donnée personnelle affichée.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${IDR_BACKUP_CONFIG:-$HOME/.config/idreader/backup.env}"
HOST_TAG="idreader"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*"; }
die() { log "erreur : $*" >&2; exit 1; }

[[ -f "$CONFIG" ]] || die "configuration absente : $CONFIG (voir deploy/BACKUP.md)."
perm="$(stat -f '%Lp' "$CONFIG" 2>/dev/null || echo 600)"
[[ "$perm" == "600" ]] || die "$CONFIG doit avoir les droits 600 (actuellement $perm)."
# shellcheck disable=SC1090
set -a; source "$CONFIG"; set +a
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY manquant dans $CONFIG}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE manquant dans $CONFIG}"
[[ -f "$RESTIC_PASSWORD_FILE" ]] || die "fichier de mot de passe restic absent."
command -v restic >/dev/null || die "restic absent : brew install restic"
command -v sqlite3 >/dev/null || die "sqlite3 absent."

# Disque externe débranché : on s'arrête net plutôt que d'écrire sur le disque interne.
if [[ "$RESTIC_REPOSITORY" == /Volumes/* ]]; then
  volume="/Volumes/$(echo "${RESTIC_REPOSITORY#/Volumes/}" | cut -d/ -f1)"
  mount | grep -q " on $volume " || die "disque de sauvegarde non monté ($volume)."
fi

if [[ "${1:-}" == "--init" ]]; then
  if restic cat config >/dev/null 2>&1; then
    log "dépôt restic déjà initialisé"
  else
    restic init
    log "dépôt restic créé : conserver le mot de passe hors du Mac (coffre de l'entreprise)"
  fi
  exit 0
fi
restic cat config >/dev/null 2>&1 || die "dépôt restic non initialisé : deploy/backup.sh --init"

cd "$REPO/backend"
idr() { uv run --frozen --no-sync idreader "$@"; }
data_dir="$(idr show-config | uv run --frozen --no-sync python -c \
  'import json, sys; print(json.load(sys.stdin)["data_dir"])')"
[[ -n "$data_dir" && -d "$data_dir" ]] || die "dossier des données introuvable (idreader show-config)."
read -r images_days data_days < <(idr purge --show-retention)
golden_days="${GOLDEN_KEEP_DAYS:-$((data_days + 7))}"

stage="$data_dir/tmp/backup"
rm -rf "$stage"
mkdir -p "$stage"
chmod 700 "$data_dir/tmp" "$stage"
trap 'rm -rf "$stage"' EXIT

# 1. Base : copie cohérente (jamais une copie à chaud du fichier), vérifiée.
log "copie de la base"
sqlite3 "$data_dir/idreader.sqlite3" ".backup '$stage/idreader.sqlite3'"
[[ "$(sqlite3 "$stage/idreader.sqlite3" 'PRAGMA integrity_check;')" == "ok" ]] \
  || die "copie de la base corrompue : sauvegarde annulée."
chmod 600 "$stage/idreader.sqlite3"
data_paths=("$stage/idreader.sqlite3")
[[ -f "$REPO/.env" ]] && data_paths+=("$REPO/.env")

# 2. Instantanés.
log "instantané idreader-data"
restic backup --quiet --host "$HOST_TAG" --tag idreader-data "${data_paths[@]}"
if [[ -d "$data_dir/batches" ]]; then
  log "instantané idreader-images"
  restic backup --quiet --host "$HOST_TAG" --tag idreader-images --exclude "$data_dir/tmp" \
    "$data_dir/batches"
fi
if [[ -d "$data_dir/golden" ]]; then
  log "instantané idreader-golden"
  restic backup --quiet --host "$HOST_TAG" --tag idreader-golden "$data_dir/golden"
fi

# 3. Oubli : durée de conservation + 7 jours (relative au dernier instantané de chaque famille).
log "oubli des instantanés (images ${images_days}+7 j, données ${data_days}+7 j, golden ${golden_days} j)"
restic forget --quiet --host "$HOST_TAG" --tag idreader-images --keep-within "$((images_days + 7))d" --prune
restic forget --quiet --host "$HOST_TAG" --tag idreader-data --keep-within "$((data_days + 7))d" --prune
restic forget --quiet --host "$HOST_TAG" --tag idreader-golden --keep-within "${golden_days}d" --prune

# 4. Contrôle hebdomadaire (dimanche) : structure du dépôt + 5 % des données relues.
if [[ "$(date +%u)" == "7" ]]; then
  log "contrôle du dépôt"
  restic check --read-data-subset=5%
fi

log "sauvegarde terminée"
