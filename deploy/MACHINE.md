# Machine — relevé

> **Attention : la machine relevée le 2026-11-24 n'est pas le Mac mini cible.** C'est un PC Windows de développement.
> Le relevé du Mac mini (prérequis de `docs/06_DEPLOIEMENT_MAC_MINI.md`) reste **à faire** sur le Mac lui-même.

## Poste de développement (relevé du 2026-11-24)

| Élément | Valeur | Commande |
|---|---|---|
| Système | Windows 11 Éducation 10.0.26300 (pas macOS) | `Get-CimInstance Win32_OperatingSystem` (`sw_vers` absent) |
| Processeur | Intel Core i5-12400F, x86_64 (pas Apple Silicon) | `Get-CimInstance Win32_Processor` |
| Mémoire vive | 31,8 Go | `Win32_ComputerSystem.TotalPhysicalMemory` |
| Disque C: | 11,7 Go libres (220 Go utilisés) | `Get-PSDrive C` |
| Xcode CLT / Swift | absent (impossible sous Windows : Apple Vision n'existe que sur macOS) | `swift --version` |
| Homebrew | absent (sans objet sous Windows) | `brew --version` |
| Tesseract | absent | `tesseract --version` |
| uv | 0.13.6 | `uv --version` |
| Python | 3.14.6 (le projet vise 3.12, `uv` l'installera) | `python --version` |
| Node | v24.19.0 | `node --version` |
| pnpm | 12.18.0 | `pnpm --version` |
| Caddy | absent | `caddy version` |
| git | 2.55.0.windows.3 | `git --version` |

Conséquences : le helper Swift `idvision` ne peut être ni compilé ni lancé ici. Le backend Python et le front peuvent être développés et testés ; tout ce qui touche à Apple Vision doit être validé sur un Mac.

## Mac de développement macOS (relevé du 2026-11-25)

> Ce Mac est un **Mac de développement**, pas le Mac mini de production : le relevé de production reste à faire sur le Mac mini (FileVault, VM Lima, IDR_HOST…).

| Élément | Valeur | Commande |
|---|---|---|
| Version de macOS | 27.2.1 (build 26B131) | `sw_vers` |
| Mémoire vive | 16 Go (17 179 869 184 octets) | `sysctl hw.memsize` |
| Puce | Apple M1, 8 cœurs | `sysctl -n machdep.cpu.brand_string hw.ncpu` |
| Espace disque libre | 168 Go libres sur 460 Go | `df -h /` |
| VM Lima de préprod | aucune : `limactl` et `colima` absents, aucun processus Lima/QEMU. La CLI `docker` existe (`~/.docker/bin/docker`), non touchée. | `limactl list` |
| FileVault | **désactivé** (OK pour spécimens, à activer avant toute vraie pièce) | `fdesetup status` |
| Xcode Command Line Tools / Swift | CLT seules (pas d'Xcode.app) ; Apple Swift 6.4.2 | `swift --version` |
| Homebrew | 7.0.15 | `brew --version` |
| Tesseract | 5.5.4 (Homebrew) | `tesseract --version` |
| uv | 0.13.6 | `uv --version` |
| Node / pnpm | v24.21.0 / 12.23.0 (corepack) | `node --version; pnpm --version` |
| Caddy | installé par Homebrew le 2026-11-25 (non lancé, pas de service) | `caddy version` |

Particularité des CLT sans Xcode : `swift test` doit recevoir le plugin de macros de Swift Testing (voir `docs/DECISIONS.md`) :

```bash
swift test --package-path vision-helper -Xswiftc -plugin-path -Xswiftc /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing
```

## Mac mini M1 de production (à relever sur place)

| Élément | Valeur |
|---|---|
| macOS, RAM (8 ou 16 Go), disque, VM Lima de préprod, FileVault | à relever (`sw_vers`, `sysctl hw.memsize`, `df -h /`, `limactl list`, `fdesetup status`) |

Installation des manquants sur le Mac (pas de sudo pour Homebrew une fois installé) :

```bash
xcode-select --install                      # boîte de dialogue graphique
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # demande le mot de passe admin
brew install tesseract caddy uv node
corepack enable                             # pnpm
```

## Procédure après coupure de courant (FileVault)

À rédiger : qui saisit le mot de passe FileVault au démarrage, et comment le joindre.

## Services (après `deploy/install.sh`)

| Service | LaunchAgent | Journaux |
|---|---|---|
| API (127.0.0.1:8000) | `fr.idreader.api` | `~/Library/Logs/idreader/api.log`, `api.err.log` |
| Worker (+ `idvision serve`) | `fr.idreader.worker` | `~/Library/Logs/idreader/worker.log`, `worker.err.log` |
| Caddy (HTTPS, sous-réseau) | `fr.idreader.caddy` | `~/Library/Logs/idreader/caddy.log`, `caddy.err.log` |
| Purge (chaque jour, 2 h 00) | `fr.idreader.purge` | `~/Library/Logs/idreader/purge.log`, `purge.err.log` |
| Sauvegarde restic (chaque jour, 2 h 30) | `fr.idreader.backup` | `~/Library/Logs/idreader/backup.log`, `backup.err.log` |

```bash
launchctl print gui/$(id -u)/fr.idreader.worker | head -20      # état
launchctl kickstart -k gui/$(id -u)/fr.idreader.worker          # redémarrer
launchctl bootout gui/$(id -u)/fr.idreader.worker               # arrêter
```

Valeurs choisies à l'installation (à noter ici) : `IDR_HOST` = à relever · `IDR_LAN_CIDR` = à relever · `IDR_DATA_DIR` = à relever.
