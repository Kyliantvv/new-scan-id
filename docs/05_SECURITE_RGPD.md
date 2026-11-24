# 05 — Sécurité et RGPD

Des copies de pièces d'identité permettent une usurpation d'identité : c'est l'une des catégories de données les plus sensibles qu'une entreprise puisse stocker. Ce document fixe le minimum technique. **Le cadre juridique (finalité, base légale, durées, information des personnes, registre des traitements, analyse d'impact éventuelle) doit être validé par le DPO ou le référent RGPD de l'entreprise avant la mise en production.**

## Minimisation

- Les colonnes extraites sont configurables : désactiver une colonne = le champ n'est ni extrait ni stocké.
- Le portrait n'est **pas** extrait par défaut. Option : flouter la zone du visage dans les recadrages stockés (détection de visage par `idvision detect_faces`).
- Les artefacts de debug (sorties OCR brutes) suivent la même durée de conservation que les images.

## Conservation et purge

| Donnée | Durée par défaut (réglable) |
|---|---|
| Originaux, pages, recadrages, artefacts | 30 jours (`IDR_RETENTION_IMAGES_DAYS`) |
| Données extraites et journal d'audit | 365 jours (`IDR_RETENTION_DATA_DAYS`) |
| Jeu de test réel (`data/golden/`) | selon la décision du DPO ; liste des cas consultable et purgeable |

- Purge automatique quotidienne (job `purge`) et purge manuelle par lot.
- Purger = supprimer les fichiers **et** les lignes en base, puis `VACUUM` périodique.
- Les sauvegardes appliquent la même logique : une sauvegarde plus ancienne que la durée de conservation + 7 jours est supprimée.

## Réseau

- L'API écoute sur `127.0.0.1` ; seul Caddy écoute sur l'adresse du réseau local.
- **Pas d'exposition à Internet.** En particulier, **ne pas publier ce service par Cloudflare Tunnel ni par Dokploy** comme la préprod : le tunnel ferait transiter les pièces d'identité par un tiers, ce qui contredit l'exigence « tout en local ».
- Pare-feu macOS activé ; accès limité au sous-réseau de l'entreprise (règle Caddy `remote_ip`).
- **Aucun appel sortant** à l'exécution : vérifier pendant le banc d'essai avec un moniteur réseau (LuLu, Little Snitch ou `nettop`) que le worker et l'API n'ouvrent aucune connexion.

## Chiffrement

- FileVault activé sur le Mac mini (voir les conséquences au redémarrage dans `06_DEPLOIEMENT_MAC_MINI.md`).
- HTTPS sur le réseau local : Caddy avec `tls internal` ; installer le certificat racine de Caddy sur les postes des utilisateurs.
- Sauvegardes chiffrées (restic) sur un disque externe local.

## Accès

- Comptes nominatifs uniquement, pas de compte partagé.
- Mots de passe : argon2id, 12 caractères minimum.
- Sessions : stockées en base (table `sessions`, jeton haché), cookie `HttpOnly`, `Secure`, `SameSite=Strict`, expiration après 8 h et après 30 min d'inactivité. `IDR_COOKIE_SECURE=false` uniquement en test ou en développement local en http.
- Verrouillage temporaire après 5 échecs de connexion (`users.failed_logins`, `users.locked_until`).
- Jeton CSRF sur toutes les requêtes qui modifient des données.
- Rôles opérateur / admin (voir `01_SPEC_FONCTIONNELLE.md`).

## Journal d'audit

- Événements : connexion (réussie ou non), consultation d'un document, modification d'un champ, validation, export, purge, changement de réglage, gestion des comptes.
- Contenu : qui, quand, quoi (type d'entité + identifiant). L'avant/après des modifications est stocké en base (chiffrée par FileVault) et suit la purge des données.
- Le **journal technique** (fichiers de log) ne contient aucune valeur personnelle.

## Fichiers et uploads

- Images servies uniquement par l'API authentifiée (jamais par un dossier statique), avec `Cache-Control: no-store`.
- Noms de fichiers internes = UUID.
- Contrôles à l'upload : type réel (octets magiques), taille maximale (50 Mo par fichier), nombre de pages maximal (200), ZIP décompressé de façon sûre (taille totale, nombre de fichiers, chemins `../` refusés), rendu des PDF par pypdfium2 (pas d'exécution de JavaScript).

## Journaux techniques

- Format : horodatage, niveau, module, identifiants (`job_id`, `document_id`), code d'erreur.
- Interdit : nom, prénom, numéro, MRZ, date de naissance, nom de fichier d'origine (il contient souvent le nom de la personne).
- Rotation et conservation limitée (30 jours).
- Un test automatisé vérifie qu'aucun champ des cas synthétiques n'apparaît dans les logs après une analyse.

## Dépendances et modèles

- Versions verrouillées (`uv.lock`, `pnpm-lock.yaml`).
- Modèles téléchargés une seule fois par `scripts/fetch_models.py`, avec empreinte SHA-256 vérifiée et notée dans `models/MANIFEST.json`.
- `HF_HUB_OFFLINE=1` en production.
