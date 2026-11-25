# Avancement du projet IDReader

> Mis à jour à chaque étape. Dernière mise à jour : 2026-11-25 (premier lancement sur macOS).
> Développement hybride : tout ce qui ne dépend ni d'Apple Vision ni de vraies pièces se fait sous Windows ; le reste attend le Mac mini.

## Où on en est

| Phase | État | Commentaire |
|---|---|---|
| 0 — Socle | **validé sur macOS (2026-11-25)** | Helper Swift compilé, 9/9 tests Swift, `doctor` OK, 264 tests Python, front OK. Reste : parcours Safari/Chrome (manuel). |
| 1a — MRZ sans Vision + banc synthétique | fait | Les trois critères sont atteints (2026-11-25). |
| 1b — Vision, calibration, banc golden | à faire (Mac) | Demande macOS et de vraies pièces. |
| 2 — Zone visuelle (G, H, L) | fait sous Windows, à valider sur macOS | G (PP-OCRv6 medium det + rec, fusion), H (MRZ guidée, libellés, dates multilingues), L (confiance, statuts, raisons, alertes), documents UTO synthétiques, worker branché, `idreader analyze`, banc `documents`. Critères chiffrés : à mesurer avec Vision (deux moteurs) et sur vraies pièces. |
| 3 — Vérification humaine et exports | fait sous Windows, à valider sur macOS | Écran de vérification (zoom, cadres, alternatives, MRZ colorée, raccourcis), édition en ligne, audit avant/après, exports XLSX/CSV, réglages, comptes, statistiques, « Ajouter au jeu de test ». Les trois critères (15 s au clavier, audit visible, Excel) se vérifient à la main sur le Mac. |
| 4 — Recto/verso, doublons, sans MRZ | fait sous Windows (sans l'étape I), à valider sur macOS | Appariement J (score, auto ≥ 0,8 sans concurrent proche, suggestion sinon), job `pair_batch` à +2 min ou bouton, fusion H + L sur les deux faces, association / dissociation manuelles (touche P), doublons de document, classification sans MRZ. **Reste** : étape I (PDF417, QR, 2D-Doc) via Vision, sur le Mac. |
| 5 — Exploitation | fait sous Windows, à valider sur macOS (rien n'a été exécuté) | `deploy/install.sh` (idempotent), LaunchAgents (API, worker, Caddy, purge à 2 h, sauvegarde à 2 h 30), `Caddyfile` (`tls internal`, sous-réseau), `backup.sh` + `BACKUP.md` (restic, trois rétentions, restauration, test trimestriel), `MACHINE.md`. Purge automatique (`idreader purge`, Administration > Purge, « Purger le lot »), page Santé et alerte « macOS a changé depuis le dernier banc ». **Critères à valider sur le Mac** : retour seul après `fdesetup authrestart`, lot de 300 documents sans intervention (p95), aucun appel sortant pendant un banc complet, revue de `05_SECURITE_RGPD.md`. |
| 6 — IA locale (option) | à faire (Mac) | Décision sur mesures. |
| 7 — Gabarits | à faire | Demande les statistiques réelles. |

## Derniers résultats du banc

| Banc | Date | Commit | Moteurs actifs | Résultat | Représentatif |
|---|---|---|---|---|---|
| synthétique (MRZ), 300 cas, graine 2026 | 2026-11-25 | `f453f2d` + correctif | vision, tesseract, ppocr | MRZ entièrement correcte **81,7 %** (Windows : 80,0) · sommes valides 92,0 % (92,7) · 0 erreur auto-validée · auto-validation 0 % · p50 2,2 s (0,23 s), p95 3,7 s | **oui** (Mac, trois moteurs) ; non calibré |
| documents UTO (A→L, appariement réel), 60 documents | 2026-11-25 | `f453f2d` + correctif | vision, tesseract, ppocr | détection 100 % · champs obligatoires tous justes **40,0 %** (30,0) · cartes appariées 81,2 % (84,4) · un seul document par pièce 90,0 % (91,7) · **0 fausse paire** · **0 erreur auto-validée** · noms justes sans accents 75,0 % (73,3), avec accents 51,6 % (38,5) · auto-validation 0 % · p50 12,5 s (3,8), p95 19,8 s (5,0) | **oui** (Mac, trois moteurs) ; non calibré |
| synthétique (MRZ), 300 cas, graine 2026 | 2026-11-25 | `7859b22` | ppocr | MRZ entièrement correcte 80,0 % · sommes valides 92,7 % · 0 erreur auto-validée · p50 225 ms | **non** (Windows, sans Vision ni Tesseract) |
| documents UTO (A→L, appariement réel), 60 documents, graine 2026 | 2026-11-25 | `910e0c9` | ppocr | détection 100 % · champs obligatoires tous justes 30,0 % · cartes appariées automatiquement 84,4 % · un seul document par pièce 91,7 % · **0 fausse paire** · **0 erreur auto-validée** · noms justes 73,3 % sans accents · p50 3,8 s, p95 5,0 s | **non** |
| documents UTO (A→L, appariement idéal), 60 documents | 2026-11-25 | `c46c8b2` | ppocr | détection 100 % · champs obligatoires tous justes 30,0 % · noms justes 71,7 % sans accents, 38,5 % avec · auto-validation 0 % (un seul moteur) · **0 erreur auto-validée** · p50 3,8 s par document | **non** |

Rapports : `bench/results/2026-11-25_f453f2d-dirty_synthetic.md`, `bench/results/2026-11-25_f453f2d-dirty_documents.md` (Mac, correctif Vision non encore commité au moment du banc, d'où « dirty »), `bench/results/2026-11-24_7859b22_synthetic.md`, `bench/results/2026-11-25_910e0c9_documents.md` (date UTC dans le nom). Ces chiffres servent de référence pour suivre le code ; aucun poids ni seuil n'est calibré dessus.

## Lecture des résultats de la phase 2 (non représentatifs)

- Sans Vision, chaque valeur n'a qu'un moteur de zone visuelle : sexe, nationalité et noms restent « non prouvés », d'où 0 % d'auto-validation. C'est voulu (règle 3).
- Qualité « poor » et pages A4 (cartes à 300 dpi, petits caractères) : 0 % de documents entièrement justes avec PP-OCR seul.
- Accents : PP-OCR perd parfois un tréma ou un accent (`Ï`→`I`). La MRZ ne peut pas le voir ; d'où la règle « accents prouvés par deux moteurs » (DECISIONS du 2026-11-25).

## Ce qui reste bloqué, et pourquoi

| Point | Raison | Débloqué par |
|---|---|---|
| Apple Vision dans le pipeline (MRZ, zone visuelle, détection, orientation, codes-barres) | idem | Mac mini, phase 1b |
| Calibration des poids du vote, des seuils de qualité et de confiance | Interdite sous Windows : banc non représentatif | Banc complet sur le Mac + golden |
| Banc `golden` et critères chiffrés sur vraies pièces | Aucune vraie pièce sur ce PC (règle du projet) | Mac mini, disque chiffré |
| Tesseract sous Windows | L'installation demande les droits administrateur | `winget install --id UB-Mannheim.TesseractOCR` (facultatif) ou `brew install tesseract` sur le Mac |
| Parcours navigateur (Safari, Chrome) | Le navigateur piloté n'a pas pu être connecté sous Windows | Test manuel |

## Quand j'arrive sur le Mac

Marche à suivre, du clone jusqu'au lancement de la phase 1b. Compter environ une heure.

### 1. Prérequis

```bash
sw_vers                                   # macOS 15 minimum (26+ conseillé)
sysctl hw.memsize                         # 8 ou 16 Go : à noter
xcode-select --install                    # compilateur Swift
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # si Homebrew absent
brew install tesseract caddy uv node git
corepack enable                           # pnpm
fdesetup status                           # FileVault doit être actif avant toute vraie pièce
```

### 2. Récupérer le projet

```bash
git clone https://github.com/Kyliantvv/new-scan-id.git idreader
cd idreader
```

Remplir `deploy/MACHINE.md` : sortie de `sw_vers`, mémoire, puce, disque libre, ressources de la VM Lima.

### 3. Backend

```bash
cd backend
uv sync
cp ../.env.example ../.env                # puis générer IDR_SECRET_KEY :
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
uv run alembic upgrade head
uv run idreader create-user --admin admin@exemple.fr
```

### 4. Modèles (téléchargement unique, empreintes vérifiées)

```bash
uv run python ../scripts/fetch_models.py           # télécharge ce que liste scripts/models.toml
uv run python ../scripts/fetch_models.py --verify  # doit afficher « ok » pour chaque fichier
```

### 5. Helper Swift

```bash
cd ..
swift build -c release --package-path vision-helper
swift test --package-path vision-helper
vision-helper/.build/release/idvision doctor        # version de macOS, révisions, langues Vision
```

### 6. Liste « À vérifier sur macOS »

Ouvrir `docs/DECISIONS.md`, section « À vérifier au premier lancement sur macOS », et cocher chaque point (helper Swift, backend, front, déploiement). Tout point qui échoue est corrigé avant la phase 1b.

### 7. Tests

```bash
cd backend
uv run pytest && uv run ruff check . && uv run mypy src   # les tests Vision réels ne sont plus « skipped »
cd .. && pnpm --dir frontend install && pnpm --dir frontend build && pnpm --dir frontend test
```

Parcours manuel dans Safari et Chrome : `uv run idreader serve`, `uv run idreader worker`, `pnpm --dir frontend dev`, puis connexion, lot, dépôt, progression.

### 8. Banc synthétique avec les trois moteurs

```bash
cd backend
uv run idreader bench --set synthetic --count 300 --seed 2026
uv run idreader bench --set documents --count 60 --seed 2026
```

Les rapports doivent indiquer « Moteurs actifs : vision, tesseract, ppocr » et ne plus porter l'avertissement de non-représentativité. Comparer avec la référence Windows ci-dessus. L'installation en service (`deploy/install.sh`) vient en phase 5, une fois la phase 1b faite.

### 9. Lancer la phase 1b

Message à donner à Claude Code, ouvert dans le dépôt sur le Mac :

> Lis `CLAUDE.md`, `AVANCEMENT.md`, puis `docs/07_ROADMAP.md` (phase 1b) et les sections de `docs/03_PIPELINE_ANALYSE.md` qu'elle concerne. On est sur le Mac mini. Commence par la liste « À vérifier au premier lancement sur macOS » de `docs/DECISIONS.md` : compile le helper, fais passer ses tests, corrige ce qui échoue et coche la liste. Ensuite, propose un plan découpé en étapes vérifiables pour la phase 1b (intégration de Vision, calibration des poids du vote, banc synthétique complet, premier banc golden). Écris les tests avec le code. Un commit par étape, poussé aussitôt. Mets à jour `AVANCEMENT.md` à chaque étape. À la fin, donne-moi les critères d'acceptation avec leur état et les chiffres.

## Premier lancement sur macOS (2026-11-25, Mac de développement)

Machine : macOS 27.2.1, M1, 16 Go, 168 Go libres, pas de VM Lima (`deploy/MACHINE.md`). FileVault désactivé. Vision : 33 langues « accurate » (latin, chinois, japonais, coréen, russe, ukrainien, thaï, vietnamien, arabe, hindi, marathi…), 6 « fast » ; `RecognizeTextRequest` révision 3 ; `RecognizeDocumentsRequest` disponible.

- Auto-validation toujours à 0 % avec trois moteurs : 108 des 245 MRZ exactes du banc synthétique n'ont aucun motif. Les champs non couverts par une somme (noms, sexe, nationalité) restent sous le seuil 0,95 ; à traiter par la calibration (phase 1b), sans toucher aux seuils avant.
- Temps : p50 par document ×3,3 (3,8 → 12,5 s) : trois moteurs de zone visuelle au lieu d'un seul.
- Qualité « poor » : 5 % de documents entièrement justes.
- Le banc doit signaler un moteur actif dont toutes les lectures échouent (bug `data/tmp/` passé inaperçu).

## Lot réel de 23 documents français (2026-11-25) : diagnostic

Résultat du premier lot réel : tout « À vérifier », noms et prénoms faux (« P », « (6Z) », « e napes 12 »), nom d'usage mêlé aux prénoms, pays et dates vides, recto/verso des CNI non associés. **Aucune image ni valeur de ce lot n'a été ouverte** (règle : chiffres agrégés et codes seulement).

Étape 1 faite : `uv run idreader diagnose --batch <id> [--json]` (`backend/src/idreader/diagnose.py`). Par document : type, pays (code ISO), nombre de recadrages, MRZ trouvée / valide / format, sommes en échec (noms des contrôles), étape en cause, appariement, jobs (dont `pair_batch` et son échéance), et pour chaque champ : statut, confiance, source (`mrz`, `viz:label`, `viz:mrz_guided`, `viz:type`, `human`), longueur (noms seulement) et drapeaux d'anomalie (`NUMBERING`, `LABEL_WORD`, `SHORT`, `DIGITS`, `LOWERCASE`, `DOC_CODE`, `ODD_CHAR`, `USAGE_MARK`, `DATE_FORMAT`) avec la *forme* du texte suspect (`(9A)`), jamais sa valeur. Test de fuite : sur un lot synthétique, aucune valeur, ligne de MRZ, nom de fichier ni nom de lot dans la sortie texte ni JSON.

Hypothèses à confirmer par le diagnostic (vérifiées seulement sur des chaînes synthétiques, `label_field`) :
- « Prénoms (2) » propre est bien reconnu comme libellé ; les variantes OCR ne le sont pas (`(2` sans parenthèse fermante, `{2}`, `[2]`, `Prénoms 2`, `(Z)`) et un « (2) » ou « (6Z) » seul (séparé de son libellé) n'est ni libellé ni filtré : il peut devenir une valeur (cas « (6Z) »).
- « Type : P » n'est pas un libellé connu : un « P » isolé peut être lu comme texte libre ; le drapeau `DOC_CODE` le signalera.
- « Nom ép. DUPONT » n'est pas un libellé combiné : à traiter avec le nom d'usage.

Reste (après ton retour du diagnostic) : 2. gabarits `reference/templates/` (France d'abord), 3. images de contrôle à valider par toi, 4. corrections (lecture MRZ, appariement automatique, MRZ prioritaire sur la zone imprimée), 5. autres pays.

### Corrections après le premier diagnostic (2026-11-25)

Causes classées par nombre de documents touchés (lot réel, 22 documents) :

| # | Cause | Documents | État |
|---|---|---|---|
| 1 | MRZ non localisée (morphologie seule) : tous les passeports | 18 / 22 | **corrigé** (repli par les lignes OCR) : à vérifier sur ton lot |
| 2 | Zone visuelle pauvre sans MRZ : dates, sexe, expiration vides (19-20 / 22), pays inconnu (6) | 19-20 / 22 | conséquence de 1 ; gabarits (étape suivante) pour les passeports sans MRZ lisible |
| 3 | Nom = `P` (code du document) | 8 / 22 | **corrigé** (libellés Type/Code, valeur invraisemblable) ; position réelle de `P` à lire avec le nouveau `diagnose` |
| 4 | Prénoms en casse mixte (10), marques `ép.`/`usage` (5), numéro de libellé `(2)` / chiffres (3), nationalité avec libellés (2) | 10 / 5 / 3 / 2 | **corrigé** (majuscules, usage_name, filtres) |
| 5 | Recto/verso de la CNI : trois documents pour deux recadrages (p5, p6), suggestion 0,3 restée alors qu'une paire à 0,8 existe | 3 docs | **non résolu** : hypothèse = deux recadrages par page (carte détectée deux fois) ; le nouveau `diagnose` donne le nombre de recadrages par page et de documents par recadrage |
| 6 | Format physique `unknown` (17 recadrages) et reflets (18) | 17 / 18 | pas de correction sans géométrie ; le nouveau `diagnose` donne taille du recadrage, part de la page et mode de détection |

Banc documents (Mac, trois moteurs) avant → après : champs obligatoires tous justes 40,0 → 41,7 % · noms avec accents 51,6 → 52,7 % · sans accents 75,0 % · cartes appariées 81,2 % · 0 erreur auto-validée · 0 fausse paire. Aucune régression. Le banc synthétique MRZ n'est pas concerné (le repli n'y est pas activé).

### Deuxième diagnostic du lot réel (2026-11-25)

Lecture du nouveau `diagnose` (23 documents) : MRZ trouvée sur 9 documents sur 23 (contre 3 avant), dont 6 par le repli OCR ; 14 restent `MRZ_NOT_FOUND`.

| Cause | Constat | Correction |
|---|---|---|
| 5 : CNI « détectée deux fois » | **Infirmée.** `documents_per_crop = 1` pour les 23 recadrages : aucun recadrage n'est partagé. Les pages 5 et 6 portent chacune **deux cartes** (2 recto, 2 verso, 7,2 % de la page chacune). L'appariement n'avait pas encore tourné (job à +85 s) ; auparavant il ne formait qu'une paire sur deux : aucun indice de proximité entre deux pages d'un même PDF. | Pages consécutives du même fichier + même rang sur la page (voir DECISIONS) ; tests : 2 cartes par page. |
| 6a : format physique `unknown` (11) | Les passeports sont détectés comme **la page entière** (`détection=page`, 100 %, 2338 × 1653) ; sans MRZ, le format ne peut pas être déduit. Conséquence directe de la MRZ non trouvée, pas un défaut de détection. | Aucune ; disparaît avec la MRZ. |
| 6b : reflets (18) | **Faux positifs** : les scans à fond blanc pur comptaient leur fond comme reflet. Sur le spécimen en page A4, ratio 42-74 % avant, 0 % après. | Définition du reflet (voir DECISIONS) ; tests. |
| MRZ non localisée (14) | Non reproductible sur le spécimen PRADO (4 variantes : pleine page, marges, rotation 2°, petit : MRZ lue à chaque fois). | Tolérance élargie (lignes tronquées, fusionnées, seule, chevrons lus en lettres) + **sonde OCR** dans `diagnose`. |

Banc documents avant → après ce lot de corrections : champs obligatoires tous justes 41,7 → 41,7 % ; cartes appariées 81,2 % ; 0 erreur auto-validée ; 0 fausse paire. Aucune régression.
