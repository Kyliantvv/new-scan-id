# 07 — Roadmap

Chaque phase se termine par ses critères d'acceptation. On ne commence pas la suivante avant. À la fin de chaque phase : mettre à jour les docs si la réalité a contredit le plan, et noter les résultats de banc dans `bench/`.

---

## Phase 0 — Socle

**Tâches**
- Dépôt, `backend/pyproject.toml` (uv, ruff, mypy, pytest), `frontend/` (Vite + React + TS + Tailwind), `.gitignore` (`data/`, `models/`, `.env`).
- `core/` : config Pydantic, SQLite (WAL, `foreign_keys`, `busy_timeout`), modèles ORM et première migration Alembic (`docs/02_ARCHITECTURE.md`).
- Auth : comptes, argon2id, sessions, CSRF, rôles ; commande `create-user`.
- File de jobs (`worker/queue.py`) avec battement de cœur (10 s) et reprise des jobs abandonnés (60 s sans battement) ; worker qui exécute un job factice.
- Upload multi-fichiers vers un lot, stockage en UUID, empreinte SHA-256, SSE de progression.
- Helper Swift `idvision` : `version`, `languages`, `doctor`, mode `serve` JSON lines, `recognize_text` (avec conversion des coordonnées en pixels, origine en haut à gauche).
- `engines/apple_vision.py` : client du processus `idvision serve` (démarrage, redémarrage s'il meurt, délai maximal par requête).
- `scripts/fetch_models.py` avec manifeste SHA-256.
- Front : connexion, liste des lots, page d'un lot avec zone de dépôt et tableau AG Grid vide.

**Critères d'acceptation**
- `pytest`, `ruff`, `mypy` passent.
- `idvision doctor` affiche la version de macOS et les langues disponibles.
- Un fichier déposé crée un job ; le job passe `done` ; la progression s'affiche en direct.
- Tuer le worker pendant un job puis le relancer : le job reprend.

---

## Phase 1 — MRZ de bout en bout + banc d'essai

**Tâches**
- Étapes A (ingestion), B (qualité), C (détection et recadrage), D (orientation).
- Étape E complète : localisation, lectures Vision + Tesseract + PP-OCR, vote, formats (TD1, TD2, TD3, MRV-A, MRV-B, ancienne CNI française), sommes de contrôle, angles morts, réparation, noms.
- Étape F (classification par la MRZ) et L minimal (statut à partir de la MRZ seule).
- Tableau alimenté avec les champs MRZ ; détail d'un document avec la MRZ colorée.
- `scripts/gen_mrz.py` (MRZ synthétiques + rendu OCR-B + dégradations) et `idreader bench` (sets `synthetic` et `golden`, rapport Markdown, `--compare`).
- `idreader analyze --debug`.

**Critères d'acceptation**
- Tests unitaires : vecteurs de `03_PIPELINE_ANALYSE.md` § E.8 et cas de réparation (dont un angle mort qui doit rester « À vérifier »).
- Banc synthétique : MRZ entièrement correcte ≥ 98 % sur les images de qualité moyenne.
- Premier banc `golden` (même petit, 30 à 50 vraies pièces) : résultats notés, **zéro erreur auto-validée**.
- Une page A4 avec recto et verso côte à côte donne deux recadrages.

---

## Phase 2 — Zone visuelle

**Tâches**
- Étape G : OCR de la zone visuelle par Vision et PP-OCR avec choix des langues et des modèles selon le pays, fusion en emplacements.
- Étape H : recherche guidée par la MRZ, ancrage sur les libellés (`reference/viz_labels.yaml`), analyse de dates multilingue (`reference/months.yaml`), translittération ICAO, champs particuliers (délivrance, lieu de naissance, autorité, nom d'usage).
- Étape L complète : confiance par champ, statuts, raisons, alertes.

**Critères d'acceptation**
- Sur `golden` : nom avec accents correct ≥ 95 % quand la zone visuelle est lisible ; date de délivrance et lieu de naissance mesurés et notés.
- Précision de l'auto-validation ≥ 99,5 %.
- Toute raison de « À vérifier » est compréhensible par un opérateur.

---

## Phase 3 — Vérification humaine et exports

**Tâches**
- Écran de vérification complet (`01_SPEC_FONCTIONNELLE.md`) : cadres, alternatives, MRZ colorée, raccourcis clavier, actions.
- Édition en ligne dans le tableau, journal d'audit.
- Exports XLSX et CSV.
- Bouton admin « Ajouter au jeu de test » (désactivé par défaut, réglage dédié).
- Statistiques : champs les plus corrigés, matrice de confusion MRZ apprise des corrections.

**Critères d'acceptation**
- Vérifier et valider un document « À vérifier » typique prend moins de 15 s au clavier.
- Une correction est visible dans l'audit avec l'avant/après.
- L'export XLSX s'ouvre dans Excel avec de vraies dates ; le CSV s'ouvre correctement dans Excel en français.

---

## Phase 4 — Recto/verso, codes-barres, documents sans MRZ

**Tâches**
- Étape J (appariement automatique et suggéré), association et dissociation manuelles dans l'interface. Déclenchement automatique quand tous les jobs du lot sont terminés et qu'aucun fichier n'a été ajouté depuis 2 minutes, bouton « Lancer l'appariement », relance si de nouveaux fichiers arrivent.
- Étape I (codes-barres).
- Classification sans MRZ (mots-clés, pays, format), extraction par libellés et types.
- Détection des doublons de document (pays + n° + date de naissance).

**Critères d'acceptation**
- Sur un lot test de cartes en deux fichiers (recto, verso) : ≥ 90 % des paires formées automatiquement, aucune paire fausse validée automatiquement.
- Les documents sans MRZ sortent en « À vérifier » avec les champs préremplis.

---

## Phase 5 — Exploitation

**Tâches**
- `deploy/` : plists launchd, Caddyfile (`tls internal`, restriction au sous-réseau), `install.sh`, `MACHINE.md`.
- Purge automatique, sauvegardes restic, page Santé, alerte « macOS a changé depuis le dernier banc ».
- Vérification « aucun appel sortant » pendant un banc complet.
- Revue de `05_SECURITE_RGPD.md` point par point.

**Critères d'acceptation**
- Redémarrage du Mac mini (`fdesetup authrestart`) : le service revient seul.
- Lot de 300 documents traité sans intervention ; temps p95 noté.
- Aucun appel réseau sortant observé.

---

## Phase 6 — IA locale (option, décision sur mesures)

**Tâches**
- `engines/vlm.py` avec mlx-vlm : chargement paresseux, contrôle de la mémoire libre, délai maximal, déchargement après inactivité.
- Étape K avec la règle d'ancrage.
- Comparer sur `golden` (sous-ensemble des documents « À vérifier » et sans MRZ) :
  - PaddleOCR-VL 1.6 en 4 bits (environ 1 Go) comme troisième moteur de transcription ;
  - Qwen3-VL-4B-Instruct en 4 bits pour l'extraction structurée. Repère communautaire : sur un M1 de 16 Go, environ 3,6 Go de mémoire au maximum et 20 tokens/s en génération, soit de l'ordre de 20 à 30 s par document (à remesurer ici).
- Sur 8 Go de RAM avec la VM de préprod active, ne tester que PaddleOCR-VL.

**Critère de décision**
- Activer seulement si : l'automatisation augmente de façon mesurable, la précision de l'auto-validation ne baisse pas, la mémoire reste sous contrôle pendant un gros lot, et le temps total d'un lot reste acceptable.

---

## Phase 7 — Gabarits et amélioration continue

**Tâches**
- Format `reference/templates/*.yaml` et chargement dans l'étape H.4.
- Gabarits pour les 20 modèles de documents les plus traités (statistiques de la phase 3), puis pour ceux qui génèrent le plus de corrections.
- `reference/doc_number_patterns.yaml` et `reference/doc_codes.yaml` enrichis.
- Recalibration trimestrielle des seuils sur `golden`.

**Critère d'acceptation**
- Chaque gabarit ajouté améliore le banc sur son modèle de document sans dégrader les autres.

---

## Consignes de démarrage pour Claude Code

À coller au début de chaque phase :

> Lis `CLAUDE.md`, puis `docs/07_ROADMAP.md` (phase N) et les sections de `docs/03_PIPELINE_ANALYSE.md` qu'elle concerne. Propose d'abord un plan découpé en étapes vérifiables, avec les fichiers que tu vas créer ou modifier. Écris les tests avant ou en même temps que le code. À la fin de la phase, lance `pytest`, `ruff`, `mypy` et le banc d'essai concerné, puis donne-moi les critères d'acceptation avec leur état (atteint / non atteint, chiffres à l'appui).
