#!/usr/bin/env bash
# Installation d'IDReader sur le Mac mini M1 (docs/06_DEPLOIEMENT_MAC_MINI.md § Installation).
#
# Idempotent : on peut le relancer après une mise à jour du dépôt ; chaque étape vérifie
# ce qui existe déjà. À lancer depuis le compte macOS dédié `idreader` (standard, non admin),
# dans une session ouverte : les services sont des LaunchAgents de cette session (Vision).
#
#   IDR_HOST=idreader.local IDR_LAN_CIDR=192.168.1.0/24 ADMIN_EMAIL=admin@exemple.fr \
#     deploy/install.sh
#
# Variables :
#   IDR_HOST       nom ou adresse du Mac sur le réseau local (obligatoire)
#   IDR_LAN_CIDR   sous-réseau autorisé par Caddy, CIDR séparés par des espaces (obligatoire)
#   IDR_DATA_DIR   dossier des données (défaut : ~/idreader-data), droits 700
#   ADMIN_EMAIL    premier compte administrateur, créé seulement si la base n'a aucun compte
#   SKIP_SERVICES  1 pour ne pas (re)charger les services launchd
#
# Réseau : seules les étapes d'installation (uv sync, pnpm install, fetch_models.py) en ont
# besoin. À l'exécution, les services tournent hors ligne (HF_HUB_OFFLINE=1, UV_OFFLINE=1).
# Aucune donnée personnelle n'est affichée par ce script.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${IDR_DATA_DIR:-$HOME/idreader-data}"
LOG_DIR="$HOME/Library/Logs/idreader"
AGENTS_DIR="$HOME/Library/LaunchAgents"
SERVICES=(api worker caddy purge)  # backup ajouté si ~/.config/idreader/backup.env existe
BACKUP_CONFIG="$HOME/.config/idreader/backup.env"
MIN_MACOS_MAJOR=15
MIN_FREE_GB=20

bold() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '   \033[33mattention :\033[0m %s\n' "$*"; }
die() { printf '\033[31merreur :\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Prérequis ----------------------------------------------------------------------------
bold "1. Prérequis"
[[ "$(uname -s)" == "Darwin" ]] || die "ce script vise macOS (le moteur tourne nativement, jamais dans une VM ni dans Docker)."
[[ "$(uname -m)" == "arm64" ]] || warn "processeur $(uname -m) : le Mac mini cible est Apple Silicon (arm64)."
[[ "$(id -u)" -ne 0 ]] || die "ne pas lancer en root : utiliser le compte idreader."
[[ -n "${IDR_HOST:-}" ]] || die "IDR_HOST manquant (ex. IDR_HOST=idreader.local)."
[[ -n "${IDR_LAN_CIDR:-}" ]] || die "IDR_LAN_CIDR manquant (ex. IDR_LAN_CIDR=192.168.1.0/24)."

macos="$(sw_vers -productVersion)"
major="${macos%%.*}"
info "macOS $macos"
(( major >= MIN_MACOS_MAJOR )) || die "macOS $MIN_MACOS_MAJOR minimum."
(( major >= 26 )) || warn "macOS 26 ou plus récent requis pour RecognizeDocumentsRequest (Vision)."

ram_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
info "mémoire vive : ${ram_gb} Go"
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
info "espace libre : ${free_gb} Go"
(( free_gb >= MIN_FREE_GB )) || warn "moins de ${MIN_FREE_GB} Go libres."

missing=()
for cmd in swift uv node pnpm caddy tesseract sqlite3 curl; do
  if command -v "$cmd" >/dev/null 2>&1; then
    info "$cmd : $(command -v "$cmd")"
  else
    missing+=("$cmd")
  fi
done
if (( ${#missing[@]} )); then
  warn "manquant : ${missing[*]}"
  info "xcode-select --install          # swift"
  info "brew install tesseract caddy uv node && corepack enable   # pnpm"
  die "installer les outils manquants puis relancer."
fi

if command -v restic >/dev/null 2>&1; then
  info "restic : $(command -v restic)"
else
  warn "restic absent (sauvegardes) : brew install restic"
fi
if fdesetup status 2>/dev/null | grep -q "On"; then
  info "FileVault : actif"
else
  warn "FileVault inactif : l'activer avant de déposer de vraies pièces (docs/05_SECURITE_RGPD.md)."
fi
if /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -q "enabled"; then
  info "pare-feu macOS : actif"
else
  warn "pare-feu macOS inactif (Réglages Système > Réseau > Coupe-feu)."
fi

# --- 2. Dossier des données ------------------------------------------------------------------
bold "2. Données : $DATA_DIR"
mkdir -p "$DATA_DIR" "$LOG_DIR"
chmod 700 "$DATA_DIR" "$LOG_DIR"
info "droits 700"

# --- 3. Configuration (.env) -----------------------------------------------------------------
bold "3. Configuration"
ENV_FILE="$REPO/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO/.env.example" "$ENV_FILE"
  info ".env créé depuis .env.example"
fi
chmod 600 "$ENV_FILE"

set_env() {  # set_env CLÉ VALEUR : remplace la ligne (commentée ou non) ou l'ajoute
  local key="$1" value="$2"
  if grep -Eq "^#? ?${key}=" "$ENV_FILE"; then
    KEY="$key" VALUE="$value" perl -pi -e 's/^#? ?\Q$ENV{KEY}\E=.*$/$ENV{KEY}=$ENV{VALUE}/' "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}
if ! grep -Eq '^IDR_SECRET_KEY=.{32,}' "$ENV_FILE"; then
  set_env IDR_SECRET_KEY "$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-64)"
  info "IDR_SECRET_KEY générée"
else
  info "IDR_SECRET_KEY déjà présente"
fi
set_env IDR_DATA_DIR "$DATA_DIR"
set_env IDR_BIND "127.0.0.1:8000"
set_env IDR_COOKIE_SECURE "true"
set_env HF_HUB_OFFLINE "1"
set_env IDR_IDVISION_PATH "$REPO/vision-helper/.build/release/idvision"
set_env IDR_TESSERACT_CMD "$(command -v tesseract)"
set_env IDR_MODELS_DIR "$REPO/models"
if grep -Eq '^IDR_ALLOW_SPECIMEN=true' "$ENV_FILE"; then
  warn "IDR_ALLOW_SPECIMEN=true : réservé aux démonstrations, jamais en production."
fi

# --- 4. Backend ------------------------------------------------------------------------------
bold "4. Backend (uv sync, migrations)"
(cd "$REPO/backend" && uv sync --frozen && uv run --frozen alembic upgrade head)

# --- 5. Helper Swift idvision ----------------------------------------------------------------
bold "5. Helper Swift idvision"
swift build -c release --package-path "$REPO/vision-helper"
"$REPO/vision-helper/.build/release/idvision" doctor

# --- 6. Front --------------------------------------------------------------------------------
bold "6. Front"
pnpm --dir "$REPO/frontend" install --frozen-lockfile
pnpm --dir "$REPO/frontend" build

# --- 7. Modèles ------------------------------------------------------------------------------
bold "7. Modèles (empreintes SHA-256 de scripts/models.toml)"
cd "$REPO/backend"
if ! uv run --frozen python ../scripts/fetch_models.py --verify; then
  info "modèles absents ou différents : téléchargement vérifié"
  uv run --frozen python ../scripts/fetch_models.py
  uv run --frozen python ../scripts/fetch_models.py --verify
fi

# --- 8. Premier administrateur ---------------------------------------------------------------
bold "8. Comptes"
DB="$DATA_DIR/idreader.sqlite3"
users=$(sqlite3 "$DB" "SELECT count(*) FROM users;" 2>/dev/null || echo 0)
if (( users > 0 )); then
  info "$users compte(s) déjà présent(s)"
elif [[ -n "${ADMIN_EMAIL:-}" ]]; then
  info "création du premier administrateur (mot de passe demandé)"
  uv run --frozen idreader create-user --admin "$ADMIN_EMAIL"
else
  warn "aucun compte : relancer avec ADMIN_EMAIL=… ou uv run idreader create-user --admin <adresse>"
fi

# --- 9. Services launchd ---------------------------------------------------------------------
if [[ "${SKIP_SERVICES:-0}" == "1" ]]; then
  bold "9. Services : ignorés (SKIP_SERVICES=1)"
  exit 0
fi
bold "9. Services launchd (LaunchAgents de la session $(id -un))"
mkdir -p "$AGENTS_DIR"
domain="gui/$(id -u)"
if [[ -f "$BACKUP_CONFIG" ]]; then
  SERVICES+=(backup)
else
  warn "sauvegarde non programmée : créer $BACKUP_CONFIG (deploy/BACKUP.md) puis relancer."
fi
UV_BIN="$(command -v uv)"
CADDY_BIN="$(command -v caddy)"
IDR_HOST="$IDR_HOST" IDR_LAN_CIDR="$IDR_LAN_CIDR" \
  caddy validate --config "$REPO/deploy/Caddyfile" --adapter caddyfile >/dev/null \
  || die "Caddyfile invalide (voir la sortie de caddy validate)."
for svc in "${SERVICES[@]}"; do
  label="fr.idreader.$svc"
  target="$AGENTS_DIR/$label.plist"
  tmp="$(mktemp)"
  sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" -e "s#__UV__#$UV_BIN#g" \
      -e "s#__CADDY__#$CADDY_BIN#g" -e "s#__IDR_HOST__#$IDR_HOST#g" \
      -e "s#__IDR_LAN_CIDR__#$IDR_LAN_CIDR#g" \
      "$REPO/deploy/launchd/$label.plist" >"$tmp"
  plutil -lint "$tmp" >/dev/null
  if launchctl print "$domain/$label" >/dev/null 2>&1; then
    launchctl bootout "$domain/$label" || true
  fi
  mv "$tmp" "$target"
  chmod 644 "$target"
  launchctl bootstrap "$domain" "$target"
  launchctl enable "$domain/$label"
  info "$label chargé"
done

# --- 10. Vérification ------------------------------------------------------------------------
bold "10. Vérification"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
(( ok )) || die "l'API ne répond pas : voir $LOG_DIR/api.err.log"
info "API : OK (127.0.0.1:8000)"
ROOT_CRT="$HOME/Library/Application Support/Caddy/pki/authorities/local/root.crt"
for _ in $(seq 1 15); do [[ -f "$ROOT_CRT" ]] && break; sleep 2; done
if [[ -f "$ROOT_CRT" ]] && curl -fsS --cacert "$ROOT_CRT" "https://$IDR_HOST/api/health" >/dev/null 2>&1; then
  info "HTTPS : OK (https://$IDR_HOST)"
else
  warn "HTTPS ne répond pas encore : voir $LOG_DIR/caddy.err.log (résolution de $IDR_HOST ?)"
fi

cat <<EOF

Installation terminée.

À faire à la main (compte administrateur macOS, une seule fois) :
  sudo pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 womp 1
  Désactiver l'installation automatique des mises à jour de macOS.

Certificat racine à installer sur les postes des utilisateurs (HTTPS local) :
  $ROOT_CRT

Page Santé (administrateur) : https://$IDR_HOST/admin
Journaux : $LOG_DIR
EOF
