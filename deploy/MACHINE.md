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

## Mac mini M1 (à relever sur place)

| Élément | Valeur | Commande |
|---|---|---|
| Version de macOS | à relever | `sw_vers` |
| Mémoire vive (8 ou 16 Go) | à relever | `sysctl hw.memsize` |
| Puce | à relever | `sysctl -n machdep.cpu.brand_string` |
| Espace disque libre | à relever | `df -h /` |
| Ressources de la VM Lima de préprod | à relever | `limactl list` |
| Xcode Command Line Tools | à relever | `swift --version` |
| Homebrew, tesseract, uv, node/pnpm, caddy | à relever | `brew --version; tesseract --version; uv --version; node --version; pnpm --version; caddy version` |

Installation des manquants sur le Mac (pas de sudo pour Homebrew une fois installé) :

```bash
xcode-select --install                      # boîte de dialogue graphique
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # demande le mot de passe admin
brew install tesseract caddy uv node
corepack enable                             # pnpm
```

## Procédure après coupure de courant (FileVault)

À rédiger : qui saisit le mot de passe FileVault au démarrage, et comment le joindre.
