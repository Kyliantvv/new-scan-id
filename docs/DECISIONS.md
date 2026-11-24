# Décisions

Une ligne par décision. Les documents concernés sont mis à jour en même temps.

| Date | Décision | Raison |
|---|---|---|
| 2026-11-24 | Développement hybride : backend et front codés et testés sous Windows, helper Swift écrit sous Windows et validé au premier lancement sur macOS. Le code vise macOS (`pathlib`, aucun code propre à Windows, scripts de déploiement macOS). | Le poste de développement actuel est un PC Windows ; la production est le Mac mini M1. |
| 2026-11-24 | `.gitattributes` avec `* text=auto eol=lf`. | Éviter les fins de ligne CRLF quand le code passe sur le Mac. |
| 2026-11-24 | Aucun modèle téléchargé sur le PC Windows ; `fetch_models.py` testé avec des URL `file://`. | Disque trop juste ; les modèles ne servent que sur le Mac. |
| 2026-11-24 | Jobs : battement de cœur (`heartbeat_at`) toutes les 10 s, job abandonné après 60 s sans battement, colonne `worker_id`. | La règle « `locked_at` > 10 min » empêchait la reprise immédiate exigée par le critère d'acceptation de la phase 0. |
| 2026-11-24 | Table `sessions` (jeton haché, CSRF, expirations) ; colonnes `users.failed_logins` et `users.locked_until`. | Le modèle de données ne permettait ni les sessions serveur ni le verrouillage après 5 échecs. |
| 2026-11-24 | Doublon de document (pays + n° + date de naissance) = alerte + `duplicate_of_id`. Statut « Doublon » réservé au fichier identique (même SHA-256). | Contradiction entre 01 (statut) et 03 (alerte). |
| 2026-11-24 | HEIC décodé par ImageIO dans `idvision` (`decode_image`), sans `pillow-heif`. Phase 1. | Les wheels de `pillow-heif` embarquent libheif/libde265 (LGPL) et x265 (GPL). |
| 2026-11-24 | Exception acceptée : FFmpeg (LGPL) embarqué par `opencv-python-headless`. | Outil interne non distribué ; FFmpeg y est une bibliothèque dynamique LGPL non modifiée. |
| 2026-11-24 | Licences acceptées : MIT, BSD, Apache-2.0, ISC, MIT-CMU, PSF. LGPL seulement en bibliothèque dynamique non modifiée et documentée. GPL et AGPL interdites. | `anyascii` (ISC) et Pillow (MIT-CMU) sont permissives mais hors de l'ancienne liste. |
| 2026-11-24 | Ajout au tableau des dépendances : `python-multipart` (Apache-2.0), `pydantic-settings` (MIT), `PyYAML` (MIT). | Nécessaires et absentes du tableau. |
| 2026-11-24 | Modèles : manifeste versionné `scripts/models.toml` (URL, SHA-256, licence) ; `fetch_models.py --pin` télécharge et affiche l'empreinte à relire et commiter ; `--verify` refuse tout écart. | On ne peut pas inventer d'empreinte de référence ; elle doit être relue par un humain. |
| 2026-11-24 | PP-OCRv6 : modèles ONNX publiés par PaddlePaddle sur Hugging Face ; si RapidOCR ne les charge pas, passage direct par onnxruntime. Vérification en phase 1 sur le Mac. | Disponibilité de PP-OCRv6 dans RapidOCR non vérifiée. |
| 2026-11-24 | `physical_format = unknown` tant que le format de MRZ n'a pas tranché ; le rapport largeur/hauteur ne sert qu'à choisir la taille canonique. | ID-2 et ID-3 ont le même rapport (≈ 1,42). |
| 2026-11-24 | Siècle de naissance : `20AA`, sauf si la date tombe après aujourd'hui, alors `19AA` ; la zone visuelle a le dernier mot. Sans confirmation de la zone visuelle, âge < 2 ans ou > 98 ans → « À vérifier ». | L'ancienne règle donnait 2026 pour une personne née en 1926 ; les âges extrêmes sont les cas où le siècle est ambigu. |
| 2026-11-24 | `IDR_COOKIE_SECURE` (vrai par défaut, faux seulement en test ou en développement local en http). | Le client de test HTTP n'envoie pas les cookies `Secure` en http. |
| 2026-11-24 | Progression SSE : l'API interroge la table `jobs` toutes les secondes. | API et worker sont deux processus ; pas de bus de messages à maintenir. |
| 2026-11-24 | Appariement (phase 4) : lancé quand tous les jobs du lot sont terminés et qu'aucun fichier n'a été ajouté depuis 2 minutes, ou par le bouton « Lancer l'appariement » ; relancé si de nouveaux fichiers arrivent. | Un lot reste ouvert : il faut un critère pour savoir quand apparier. |

## Choix non couverts par les docs (option la plus simple)

| Date | Décision | Raison |
|---|---|---|

## À vérifier au premier lancement sur macOS

À valider d'un coup sur le Mac, avant d'attaquer la phase 1.

- [ ] Relevé de la machine dans `deploy/MACHINE.md` (`sw_vers`, `sysctl hw.memsize`, puce, disque, VM Lima).
