# Sauvegardes et restauration

> Écrit sous Windows, **pas encore exécuté** : à dérouler une première fois sur le Mac mini, puis tous les trimestres (test de restauration).

## Principe

- Outil : [restic](https://restic.net) (BSD-2-Clause), dépôt chiffré sur un **disque externe** branché au Mac mini. Aucun envoi réseau.
- Chaque nuit, `fr.idreader.purge` (2 h 00) applique la durée de conservation, puis `fr.idreader.backup` (2 h 30) lance `deploy/backup.sh`. La sauvegarde ne contient donc jamais ce que la purge vient de supprimer.
- Trois familles d'instantanés, chacune oubliée à **sa durée de conservation + 7 jours** (`docs/05_SECURITE_RGPD.md`). Les durées viennent des réglages d'administration (`idreader purge --show-retention`).

| Étiquette | Contenu | Gardé |
|---|---|---|
| `idreader-data` | base SQLite (copie cohérente `sqlite3 .backup`, vérifiée par `PRAGMA integrity_check`) et `.env` | données + 7 j (372 j par défaut) |
| `idreader-images` | `data/batches/` : originaux, pages, recadrages, artefacts d'analyse | images + 7 j (37 j par défaut) |
| `idreader-golden` | `data/golden/` : jeu de test réel | `GOLDEN_KEEP_DAYS` (décision du DPO ; par défaut comme les données) |

- `--keep-within` de restic compte à partir du **dernier instantané** de chaque famille, pas de la date du jour : tant que la sauvegarde tourne chaque nuit, c'est équivalent.
- Le dimanche, `restic check --read-data-subset=5%` relit une partie des données.
- Le disque externe débranché arrête la sauvegarde avec une erreur (`~/Library/Logs/idreader/backup.err.log`) au lieu d'écrire sur le disque interne.

## Mise en place (une fois, compte `idreader`)

1. Disque externe formaté en **APFS (chiffré)** depuis Utilitaire de disque, nommé par exemple `IDReader-Sauvegarde`. Le mot de passe du disque va dans le coffre de l'entreprise.
2. Outils et configuration :

   ```bash
   brew install restic
   mkdir -p ~/.config/idreader && chmod 700 ~/.config/idreader
   # Mot de passe du dépôt restic : long, aléatoire, conservé AUSSI hors du Mac (coffre).
   openssl rand -base64 48 > ~/.config/idreader/restic-password
   chmod 600 ~/.config/idreader/restic-password
   cat > ~/.config/idreader/backup.env <<'EOF'
   RESTIC_REPOSITORY=/Volumes/IDReader-Sauvegarde/restic
   RESTIC_PASSWORD_FILE=/Users/idreader/.config/idreader/restic-password
   # GOLDEN_KEEP_DAYS=372
   EOF
   chmod 600 ~/.config/idreader/backup.env
   ```

3. Créer le dépôt, puis programmer la sauvegarde :

   ```bash
   deploy/backup.sh --init
   deploy/install.sh            # avec les mêmes variables qu'à l'installation : ajoute fr.idreader.backup
   deploy/backup.sh             # première sauvegarde à la main, pour voir la sortie
   restic snapshots             # (après `set -a; source ~/.config/idreader/backup.env; set +a`)
   ```

> Sans le mot de passe du dépôt, les sauvegardes sont illisibles : c'est voulu (pièces d'identité), mais il faut deux copies du mot de passe hors du Mac.

## Restaurer

Toujours restaurer **dans un dossier vide**, vérifier, puis remplacer. Les services sont arrêtés pendant le remplacement.

```bash
set -a; source ~/.config/idreader/backup.env; set +a
restic snapshots --tag idreader-data                     # choisir un instantané (ID)
mkdir -p ~/restauration && chmod 700 ~/restauration
restic restore <ID> --target ~/restauration              # base + .env
restic restore latest --tag idreader-images --target ~/restauration
sqlite3 ~/restauration/*/idreader.sqlite3 'PRAGMA integrity_check;'   # doit répondre « ok »

# Remplacement (services arrêtés)
for s in api worker caddy; do launchctl bootout gui/$(id -u)/fr.idreader.$s; done
# copier la base dans IDR_DATA_DIR/idreader.sqlite3 et les dossiers batches/ restaurés
# (les chemins d'origine sont recréés sous ~/restauration), puis :
cd backend && uv run alembic upgrade head && cd ..
IDR_HOST=… IDR_LAN_CIDR=… deploy/install.sh              # recharge les services et vérifie la santé
rm -rf ~/restauration
```

- Une restauration remet des données que la purge a pu supprimer depuis : relancer `uv run idreader purge` juste après.
- Restaurer une base sans ses images est possible : les documents restent lisibles, les images affichent « Image introuvable (purgée ?) ».

## Test de restauration (chaque trimestre)

1. Restaurer le dernier instantané `idreader-data` dans `~/restauration` (sans remplacer la production).
2. `PRAGMA integrity_check` répond `ok` ; `sqlite3 … 'SELECT count(*) FROM documents;'` est cohérent avec la page Statistiques.
3. Restaurer une image d'un lot récent et l'ouvrir.
4. Supprimer `~/restauration` et noter la date et le résultat ci-dessous.

| Date | Instantané | Résultat | Par |
|---|---|---|---|
| à faire | | | |
