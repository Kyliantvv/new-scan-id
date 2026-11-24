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
| 2026-11-24 | Phase 1 découpée : 1a sous Windows (E.3–E.7, A, B, C en OpenCV, Tesseract, PP-OCR, `gen_mrz.py`, banc synthétique), 1b sur macOS (Vision, calibration, banc complet et golden). | Le Mac mini n'est pas encore disponible ; avancer sur tout ce qui ne dépend pas d'Apple Vision. |
| 2026-11-24 | Exception : sous Windows, téléchargement autorisé de `mrz.traineddata` et des seuls modèles PP-OCR utiles à la MRZ, via `fetch_models.py --pin`, empreintes montrées avant commit. Aucun modèle d'IA générative. | Nécessaire pour tester Tesseract et PP-OCR ; quelques dizaines de Mo. |
| 2026-11-24 | Banc sous Windows sans Vision : chiffre de référence, pas d'objectif ; aucun poids ni seuil définitif calibré dessus. | Résultats non représentatifs du Mac mini (moteur n° 1 absent). |

## Choix non couverts par les docs (option la plus simple)

| Date | Décision | Raison |
|---|---|---|
| 2026-11-24 | Poste Windows : Python 3.12.10 officiel (python.org, installé par `winget --scope user`) au lieu du Python géré par uv ; config utilisateur `%APPDATA%\uv\uv.toml` (`python-preference = "only-system"`, `no-binary-package = ["mypy"]`) ; lancer les outils par `uv run python -m pytest` / `uv run python -m mypy`. Rien de tout cela n'est dans le dépôt. | La stratégie de contrôle d'applications de Windows bloque les exécutables non signés : `python.exe` des venv uv, les lanceurs `pytest.exe`, les DLL mypyc de mypy. Sans objet sur macOS. |
| 2026-11-24 | Identifiants internes : UUID v4 en texte pour toutes les tables (entier auto-incrémenté pour `audit_log`). Horodatages stockés en UTC, relus avec fuseau ; un horodatage sans fuseau est refusé. | Les noms de fichiers stockés dérivent des UUID ; SQLite ne garde pas le fuseau. |
| 2026-11-24 | Colonne `files.duplicate_of_id` (fichier identique, même SHA-256, déjà dans le lot) et `jobs.batch_id` (filtre SSE). | Nécessaires au statut « Doublon » de fichier et à la progression par lot. |
| 2026-11-24 | Sessions ORM en `BEGIN IMMEDIATE` par défaut ; lectures seules (SSE) en transaction différée. Transactions courtes : le fichier est écrit sur disque avant la transaction d'insertion. | En WAL, une transaction différée qui lit puis écrit échoue sans attendre si un autre processus a écrit entre-temps. |
| 2026-11-24 | Verrouillage après 5 échecs : 15 minutes (`IDR_LOGIN_LOCK_MINUTES`). Erreur identique pour e-mail inconnu et mauvais mot de passe ; message explicite pour compte verrouillé ou désactivé. | La durée n'était pas fixée ; outil interne, la clarté pour l'opérateur prime. |
| 2026-11-24 | Jeton de session aléatoire (256 bits) dans un cookie `idr_session` ; la base ne garde que son HMAC-SHA256 (clé `IDR_SECRET_KEY`). CSRF : jeton par session renvoyé par `/api/auth/login` et `/api/me`, exigé dans l'en-tête `X-CSRF-Token` pour toute méthode autre que GET/HEAD/OPTIONS. | Option la plus simple compatible avec `SameSite=Strict` et un front sur la même origine. |
| 2026-11-24 | Journal d'accès d'uvicorn désactivé, remplacé par un journal maison sans chaîne de requête ; exceptions inattendues journalisées sans leur message (type + pile) ; erreurs de validation renvoyées sans les valeurs reçues. | `?q=` peut contenir un nom ; les messages d'exception SQLAlchemy/Pydantic contiennent les valeurs. |
| 2026-11-24 | `idreader serve` et `create-user` refusent de démarrer si la base n'est pas à la dernière révision Alembic (pas de migration automatique). | La migration reste une étape explicite (`deploy/install.sh`). |
| 2026-11-24 | Tests : avertissements traités comme des erreurs, sauf l'avertissement de Starlette 1.x qui recommande `httpx2` pour `TestClient`. | `httpx` (BSD-3) fonctionne ; `httpx2` n'est pas encore évalué (licence, maturité). |
| 2026-11-24 | Upload, phase 0 : PDF, JPEG, PNG reconnus par octets magiques ; HEIC, TIFF, WEBP, ZIP refusés (`UNSUPPORTED_FORMAT`) jusqu'à la phase 1. Un fichier refusé ne laisse ni ligne en base ni fichier sur disque. | Formats prioritaires de la spec ; les autres demandent idvision ou une décompression sûre. |
| 2026-11-24 | Fichier identique (même SHA-256) dans le même lot : ligne `files` avec `duplicate_of_id`, pas de seconde copie sur disque, pas de job. Dans un autre lot, le fichier est traité normalement. | Minimisation des données ; la spec limite le doublon de fichier au lot. |
| 2026-11-24 | Starlette place les fichiers déposés de plus de 1 Mo dans le dossier temporaire du système pendant la requête, puis les supprime. Accepté : même disque chiffré par FileVault. | Comportement standard de FastAPI/Starlette ; l'éviter demanderait un analyseur multipart maison. |
| 2026-11-24 | Flux SSE limité à 30 min (`IDR_SSE_MAX_SECONDS`) avec `retry: 3000` ; le navigateur se reconnecte seul et reçoit l'état complet du lot à la reconnexion. Commentaire `ping` toutes les 15 s. | Borne les ressources côté serveur sans perte d'information. |
| 2026-11-24 | `setup_logging` force le logger `httpx` au niveau WARNING. | httpx journalise les URL complètes, chaîne de requête comprise. |
| 2026-11-24 | `setup_logging` force aussi le logger `alembic` au niveau WARNING. | Le contrôle de schéma au démarrage du worker remplissait le journal de lignes INFO inutiles. |
| 2026-11-24 | `fetch_models.py` : bibliothèque standard seulement, schémas `https://` et `file://` (tests) ; destination relative sous `models/`, sans `..` ; fichier téléchargé dans un dossier temporaire sous `models/`, déplacé seulement si l'empreinte correspond ; `models/MANIFEST.json` liste les modèles installés et vérifiés. | Script lancé une fois, avant que l'environnement Python du projet n'existe forcément. |
| 2026-11-24 | Front : React Router pour les pages (`/`, `/lots/:id`) ; envoi des fichiers un par un en `XMLHttpRequest` (3 en parallèle) pour afficher la progression d'envoi ; progression d'analyse par `EventSource`. | `fetch` ne donne pas la progression d'envoi ; un fichier par requête isole les refus. |
| 2026-11-24 | AG Grid : thème Quartz avec la police de l'interface et `loadThemeGoogleFonts={false}` ; module `AllCommunityModule` complet (bundle ≈ 1,5 Mo, 450 Ko gzip). Découpage du bundle reporté. | Aucune requête vers Google Fonts (règle 1) ; taille acceptable sur le réseau local. |
| 2026-11-24 | Tests : `IDR_FRONTEND_DIST` pointe vers un dossier absent par défaut, pour que les tests ne dépendent pas d'un build du front présent ou non. | Résultat des tests identique avec ou sans `frontend/dist`. |
| 2026-11-24 | MRZ, statuts de champ : `valid`, `repaired`, `unverified` (non couvert par une somme et lu par un seul moteur), `to_review`, `invalid` ; confiances de départ de § L, plus 0,95 / 0,80 pour les champs non contrôlés mais contraints (pays, sexe, code) avec ou sans vote unanime. | Il fallait une valeur pour les champs que la norme ne protège pas (nationalité et sexe en TD3, noms) ; à recalibrer sur golden. |
| 2026-11-24 | Angle mort (E.5) : dans une position alphanumérique couverte par une somme, un caractère qui a un partenaire indiscernable (`S/8`, `G/6`, `B/1`, `Z/5`, `U/0`, `K/<`) n'est prouvé que par deux moteurs différents d'accord, sans moteur contre, ou par le motif du pays (`reference/doc_number_patterns.yaml`). Sinon « À vérifier ». Le remplissage final du champ n'est pas concerné. | Application directe de E.5 ; conséquence : avec un seul moteur, presque tout n° de document passe « À vérifier ». |
| 2026-11-24 | `K` de remplissage → `<` seulement s'il suit au moins trois `<` et précède `<` ou la fin de ligne. | Deux `<` au plus séparent les parties d'un nom : un prénom d'une lettre `K` n'est jamais touché. |
| 2026-11-24 | Réalignement : `<` ajoutés ou retirés dans la plus longue séquence d'au moins deux `<` (la dernière à égalité). | Option la plus simple ; le remplissage final est le plus fréquent. |
| 2026-11-24 | Réparation : recherche minimale (1 position, puis 2) ; une variante unique au plus petit nombre de changements est acceptée. Si la composite échoue alors que tous les champs sont justes, la recherche porte sur les positions que seule la composite contrôle (données facultatives du TD1, chiffre composite). | Une correction minimale qui valide une somme change forcément de classe : elle est prouvée. |
| 2026-11-24 | Siècle : la zone visuelle (année sur 4 chiffres) est un paramètre optionnel de `decode` ; tant qu'elle manque (phase 1), l'âge < 2 ans ou > 98 ans met la date « À vérifier ». | Règle validée le 2026-11-24, appliquée sans zone visuelle. |
| 2026-11-24 | Ancienne CNI française : plusieurs prénoms séparés par `<<` dans `synth.py` (non vérifié, le spécimen n'a qu'un prénom). | À confirmer sur un autre spécimen public. |
| 2026-11-24 | Ingestion : PDF « scan » = page dont les seuls objets sont une image (ni texte ni tracé) ; image extraite sans rendu, dpi déduit de la largeur de page. Images : TIFF et WEBP aussi lus par Pillow (l'upload ne les accepte pas encore). Garde-fou de 150 Mpx contre les images bombes. | Option la plus simple avec pypdfium2 ; l'original reste intact. |
| 2026-11-24 | Qualité : seuils de départ non calibrés (netteté < 60 sur 1 000 px de côté long, reflet > 2 % ou tache > 0,5 %, bande MRZ > 1 %, résolution < 200 dpi). | À calibrer sur golden en phase 1b, jamais sous Windows. |
| 2026-11-24 | Détection OpenCV : deux masques (Canny + dilatation, Otsu + fermeture) sur une image réduite à 1 200 px ; quadrilatère par `approxPolyDP`, sinon rectangle d'aire minimale si le contour le remplit à 85 % (coins arrondis) ; rapport 1,25–1,80 ; ≥ 1,50 → ID-1, sinon ID-2/ID-3 redressé à la taille ID-3 ; un document en portrait reste en portrait (l'étape D le tourne) ; ordre de lecture haut-bas puis gauche-droite. | Couvre les scans A4 de cartes et de pages de passeport sans réglage par document. |
| 2026-11-25 | PP-OCR en phase 1a : modèle PP-OCRv6 **small** de reconnaissance seule (21 Mo), appelé directement par `onnxruntime` (CPU), sans RapidOCR ni modèle de détection ; une ligne MRZ à la fois après découpage par profil de projection. Prétraitement 48 px de haut, (x/255 − 0,5)/0,5, largeur ≥ 320 ; décodage CTC (blanc = 0) avec le dictionnaire de `inference.yml`. | La MRZ n'a besoin que de la reconnaissance ; le tier medium (77 Mo) dépassait l'exception « quelques dizaines de Mo ». RapidOCR reviendra pour la zone visuelle (détection + reconnaissance). Écart avec la stack de CLAUDE.md signalé ici. |
| 2026-11-25 | Tesseract n'est pas installé sous Windows : `winget install --id UB-Mannheim.TesseractOCR` exige une élévation (UAC) ; le pipeline et le banc tournent sans lui. `TesseractMrzEngine.from_settings` renvoie `None` si le binaire ou `mrz.traineddata` manque. | Pas d'installation administrateur sans accord. Sur macOS : `brew install tesseract`. |
| 2026-11-25 | Police OCR-B : `ocrb10.otf` de CTAN (OCR-B de Norbert Schwarz, contours de Zdeněk Wagner), téléchargée par `fetch_models.py` (miroir fixe, empreinte épinglée), installée dans `models/fonts/`, jamais commitée ni servie au front. | Licence « freely use, modify, and/or distribute … without limitation » ; la restriction commerciale d'origine a été levée par l'auteur. |
| 2026-11-25 | Moteurs : `engines/registry.py` construit la liste des moteurs présents (`vision` si le helper est compilé, `tesseract`, `ppocr` si leurs fichiers existent). L'étape E accepte n'importe quel sous-ensemble ; liste vide → échec `NO_ENGINE` ; un moteur qui plante est journalisé par son code et ignoré. | Règle de la phase 1a : fonctionner avec n'importe quel sous-ensemble de moteurs. |
| 2026-11-25 | E.1 sans Vision : repli morphologique (black-hat, Sobel x, fermetures, Otsu), fusion des morceaux d'une même rangée (les longues suites de `<` coupent les lignes), regroupement des rangées empilées, bande la plus basse et la plus large, marges 8 % / 3 %. Redressement fin de la bande ± 5° par maximisation de la variance du profil horizontal. | La localisation par les lignes Vision vient en phase 1b. |
| 2026-11-25 | E.2 : plan de lecture de départ vision a/b/c, tesseract a/c/d, ppocr a/b/e (9 lectures au plus avec les trois moteurs) ; prétraitements a CLAHE, b ×2 cubique, c Otsu, d seuil adaptatif, e accentuation. | Plan non calibré : à régler sur le banc golden en phase 1b. |
| 2026-11-25 | Banc synthétique : identités inventées (syllabes aléatoires), répartition des formats TD3 40, TD1 30, FRA-ID 10, TD2 8, MRV-A 7, MRV-B 5 ; trois niveaux good/medium/poor ; date de référence figée dans `truth.json`. L'automatisation du rapport approxime l'étape L par la MRZ seule (sommes valides, aucun motif, champs obligatoires `valid`/`repaired` avec confiance ≥ seuil). Le rapport ne contient ni ligne MRZ ni valeur de champ, seulement des identifiants, des indicateurs et des confusions de caractères. | Le rapport signale automatiquement qu'il n'est pas représentatif hors macOS ou sans l'un des trois moteurs. |
| 2026-11-25 | `engines/base.py` : `recognize` accepte un chemin ou un tableau NumPy ; le client Vision écrit le tableau dans un PNG temporaire de `data/tmp/` (supprimé après lecture). | Le pipeline travaille sur des tableaux ; le helper lit des fichiers. |

## À vérifier au premier lancement sur macOS

À valider d'un coup sur le Mac, avant d'attaquer la phase 1.

- [ ] Relevé de la machine dans `deploy/MACHINE.md` (`sw_vers`, `sysctl hw.memsize`, puce, disque, VM Lima).

### Outils

- [ ] Installer les outils (`deploy/MACHINE.md` § Installation) : Xcode Command Line Tools (Swift 6), Homebrew, `uv`, Node + pnpm (corepack), Tesseract, Caddy.
- [ ] `uv sync` dans `backend/` avec le Python 3.12 géré par uv (la config Windows `python-preference = "only-system"` et `no-binary-package = ["mypy"]` ne doit pas être reprise sur le Mac).
- [ ] `pytest.exe`/`mypy` lancés directement (`uv run pytest`, `uv run mypy src`) comme dans `CLAUDE.md`.

### Helper Swift `idvision` (écrit sans compilateur sous Windows)

- [ ] `swift build -c release --package-path vision-helper` compile sans avertissement en mode Swift 6 (concurrence stricte).
- [ ] `swift test --package-path vision-helper` : tests `GeometryTests` et `DispatcherTests` (Swift Testing) passent.
- [ ] Noms d'API de `RecognizeTextRequest` (macOS 15, framework Vision « Swift ») utilisés dans `AppleVisionBackend.swift`, à confirmer : `recognitionLevel`, `recognitionLanguages`, `usesLanguageCorrection`, `minimumTextHeightFraction`, `supportedRecognitionLanguages`, `revision`, `perform(on: CGImage)`, `observation.topCandidates(_:)`, `boundingBox`/`topLeft`…`bottomRight` en `NormalizedPoint` avec `.x`/`.y`.
- [ ] Format des identifiants de langue renvoyés par `supportedRecognitionLanguages` (`fr-FR`, `zh-Hans`…) : le helper reconstruit `languageCode-script-region` ; vérifier que la liste de `idvision languages` est lisible et qu'une langue de cette liste est acceptée en entrée.
- [ ] `String(describing: request.revision)` donne une valeur exploitable pour `recognize_text_revision` (sinon, choisir un autre champ).
- [ ] Détection `#available(macOS 26, *)` pour `recognize_documents_available` : compile sur le SDK installé.
- [ ] `JSONEncoder` : l'enveloppe aplatit bien la charge utile avec `id`, `ok`, `ms` (sortie d'une ligne par réponse, clés en snake_case).
- [ ] ROI découpée par `CGImage.cropping(to:)` en pixels, origine en haut à gauche ; coordonnées renvoyées dans le repère de l'image entière (décalage ajouté).
- [ ] `vision-helper/.build/release/idvision doctor` affiche la version de macOS, la révision de `RecognizeTextRequest` et les langues (critère d'acceptation de la phase 0).
- [ ] `idvision serve` : une requête invalide renvoie une erreur JSON sans quitter ; stdin fermé = sortie propre ; rien d'autre que des identifiants et codes sur stderr.
- [ ] `uv run pytest tests/unit/test_apple_vision_real.py` (passé à « skipped » sous Windows) : texte synthétique « IDREADER 2468 » retrouvé à ± 25 px, ROI respectée, `doctor` lu par le client Python.
- [ ] Le worker journalise au démarrage la version du helper et le nombre de langues (`_log_helper_status`).

### Backend et front

- [ ] Suite complète `uv run pytest` sur macOS, dont `test_worker_kill.py` (SIGKILL réel du worker puis reprise).
- [ ] `ImageFont.load_default(size=…)` de Pillow fonctionne (police intégrée, sans fichier système).
- [ ] `pnpm --dir frontend install && pnpm --dir frontend build && pnpm --dir frontend test`.
- [ ] Parcours dans Safari et Chrome : connexion, création de lot, dépôt de plusieurs fichiers, progression en direct, tableau vide en français (non fait sous Windows : le navigateur piloté n'a pas pu être connecté ; vérifié seulement par curl et les tests).
- [ ] Onglet réseau du navigateur : aucune requête hors de l'origine (pas de Google Fonts, pas de CDN).

### Plus tard (phase 1 et déploiement)

- [ ] Vision dans un LaunchAgent (session utilisateur) : le helper fonctionne lancé par launchd, pas seulement depuis le Terminal.
- [ ] `brew install tesseract`, puis `uv run idreader bench --set synthetic` avec les trois moteurs (premier banc représentatif).
- [ ] `uv run python ../scripts/fetch_models.py --verify` sur le Mac mini (mêmes empreintes que sous Windows).
- [ ] `AppleVisionEngine` avec un tableau NumPy : PNG temporaire écrit dans `data/tmp/` puis supprimé.
- [ ] FileVault actif (`fdesetup status`) avant tout dépôt de vraie pièce.
