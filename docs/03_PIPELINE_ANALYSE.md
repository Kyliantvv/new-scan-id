# 03 — Pipeline d'analyse

C'est le cœur du projet : c'est lui qui fait le taux de réussite.

## Principes

1. **La MRZ est la colonne vertébrale.** Elle figure sur tous les passeports et sur la plupart des cartes récentes. Elle est normalisée (ICAO Doc 9303), en caractères latins, à positions fixes, et protégée par des sommes de contrôle. Lisible et valide, elle **prouve** : nom et prénoms (sans accents, parfois tronqués), date de naissance, sexe, nationalité, pays émetteur, numéro du document, date d'expiration.
2. **La zone visuelle (VIZ) complète et confirme** : accents, écriture d'origine, lieu de naissance, date de délivrance, autorité, nom d'usage, adresse. Elle est lue par deux moteurs et recoupée avec la MRZ.
3. **Plusieurs lectures indépendantes et un vote valent mieux qu'une lecture « parfaite ».** Trois moteurs OCR différents et plusieurs prétraitements par zone.
4. **Chaque champ porte ses preuves.** Le statut final découle des preuves (sommes de contrôle, concordances), pas d'une impression de confiance.
5. **Générique d'abord, gabarits ensuite.** Le moteur fonctionne sans connaître le document ; des gabarits par modèle de document sont ajoutés pour les documents les plus fréquents, selon les statistiques réelles.

## Vue des étapes

```
A Ingestion → B Qualité → C Détection/recadrage → D Orientation → E MRZ
→ F Classification → G OCR zone visuelle → H Extraction des champs → I Codes-barres
→ J Appariement recto/verso (au niveau du lot) → K [option] IA locale → L Consolidation et statut
```

Chaque étape écrit son artefact JSON dans `data/batches/<lot>/artifacts/<crop>/<étape>.json`. `idreader analyze --debug` écrit aussi les images intermédiaires (bande MRZ, prétraitements, cadres).

---

## A — Ingestion

- Vérifier le type réel du fichier (octets magiques), pas l'extension.
- **PDF** : avec `pypdfium2`. Si la page contient une seule image (PDF de scan), extraire cette image **à sa résolution native** ; sinon rendre la page à 300 dpi. Limite de pages configurable (200 par défaut).
- **Images** : appliquer l'orientation EXIF, convertir en RGB 8 bits ; HEIC décodé par ImageIO via `idvision decode_image` (pas de `pillow-heif` : ses wheels embarquent du code LGPL et GPL).
- Empreinte SHA-256 de l'original → détection des doublons dans le lot.
- Côté long plafonné à 4 000 px pour le traitement (l'original reste intact).

## B — Qualité

Mesures par page puis par recadrage :
- **Netteté** : variance du laplacien (sur l'image en niveaux de gris, à taille normalisée).
- **Reflet** : part de pixels saturés (> 250 sur les trois canaux) et taille des taches ; reflet sur la bande MRZ = alerte forte.
- **Résolution estimée** : largeur du document en pixels rapportée à sa largeur physique (85,6 mm pour une carte, 125 mm pour une page de passeport). Sous ~200 dpi équivalent : alerte.

Ces mesures ne bloquent pas l'analyse : elles produisent des alertes et, si la MRZ échoue, la **raison d'échec** affichée (« reflet sur la MRZ », « image floue »…). Seuils à calibrer sur le banc d'essai.

## C — Détection et recadrage

Objectif : trouver **chaque** document de la page, le redresser et le ramener à une taille canonique.

1. `idvision detect_documents` : segmentation de document + détection de rectangles (jusqu'à 6 candidats).
2. Repli OpenCV : contours (Canny + dilatation), approximation polygonale à 4 sommets, filtre sur l'aire (> 5 % de la page) et le rapport largeur/hauteur.
3. Repli par la MRZ : si une MRZ est trouvée sur la page sans document détecté autour, déduire le cadre à partir de la géométrie de la MRZ (sa largeur représente une part connue de la largeur du document selon le format).
4. Dernier repli : la page entière, avec alerte « document non détouré ».

Supprimer les quadrilatères qui se chevauchent (IoU > 0,6), garder le plus confiant.

Redressement par transformation de perspective vers des tailles canoniques (environ 500 dpi) :

| Format physique | Dimensions | Taille canonique |
|---|---|---|
| ID-1 (cartes, TD1) | 85,6 × 54 mm | 1 712 × 1 080 px |
| ID-2 (TD2) | 105 × 74 mm | 2 100 × 1 480 px |
| ID-3 (page de passeport, TD3) | 125 × 88 mm | 2 500 × 1 760 px |

Le rapport largeur/hauteur (ID-1 ≈ 1,59 ; ID-2 ≈ 1,42 ; ID-3 ≈ 1,42) ne donne qu'une **hypothèse** qui sert à choisir la taille canonique du redressement. ID-2 et ID-3 ayant le même rapport, `physical_format` reste `unknown` tant que le format de MRZ (étape E) n'a pas tranché. Un passeport ouvert en double page est découpé : la page de données est celle qui porte la MRZ.

## D — Orientation

1. OCR rapide (`level: fast`) sur une version réduite du recadrage à 0°, 90°, 180° et 270°. Score = somme (confiance × nombre de caractères). Garder le meilleur.
2. Confirmation par la MRZ : la MRZ est toujours **en bas** de la face qui la porte. Si elle est trouvée en haut, tourner de 180°.
3. Redressement fin : angle moyen des lignes de texte détectées (± 5° max) puis rotation.

## E — MRZ

### E.1 Localisation

- Sur le recadrage orienté, lignes OCR candidates : au moins 25 caractères une fois les espaces retirés, au moins 3 « < » ou motif `[A-Z0-9<]{25,}`, situées dans la moitié inférieure.
- Regrouper 2 ou 3 lignes consécutives, parallèles, de longueurs proches (± 10 %).
- Bande MRZ = union des boîtes + marge de 8 % en hauteur et 3 % en largeur.
- Repli si rien n'est trouvé : détection morphologique OpenCV (gradient horizontal, fermeture, recherche de bandes larges et denses dans le tiers inférieur).

### E.2 Lectures multi-moteurs

Prétraitements de la bande (budget de 6 à 9 lectures au total) :
- (a) niveaux de gris + CLAHE ;
- (b) (a) agrandi × 2 (interpolation cubique) ;
- (c) binarisation d'Otsu ;
- (d) seuil adaptatif ;
- (e) (a) + accentuation légère.

Moteurs :
- **Vision** : `level: accurate`, `languages: ["en-US"]`, `language_correction: false`, `top_candidates: 5`.
- **Tesseract** : `-l mrz --oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<`.
- **PP-OCR** : reconnaissance seule (modèle latin), ligne par ligne sur les lignes découpées.

Normalisation de chaque sortie :
- majuscules, suppression des espaces ;
- `«`, `‹`, `≤`, `〈`, `く` et variantes → `<` ;
- tout caractère hors `[A-Z0-9<]` → remplacé par sa forme visuelle la plus proche ou supprimé ;
- un `K` isolé entre deux `<` **dans une zone de remplissage** → `<` (confusion fréquente) ; jamais à l'intérieur d'un nom ;
- longueur à ± 2 caractères de la longueur attendue → réalignement en insérant ou supprimant des `<` **uniquement dans les séquences de remplissage**.

### E.3 Vote

- Aligner les lectures de chaque ligne (même longueur après normalisation).
- Vote par position, pondéré par moteur. Poids de départ : Vision 1,0 ; Tesseract-mrz 0,9 ; PP-OCR 0,8 ; à recalibrer sur le banc d'essai.
- Conserver pour chaque position la **distribution** des caractères proposés et les candidats alternatifs de Vision : ils servent à la réparation (E.6) et aux alternatives affichées dans l'écran de vérification.

### E.4 Formats

| Format | Lignes × caractères | Documents | Somme composite |
|---|---|---|---|
| TD3 | 2 × 44 | passeports (code commençant par `P`) | oui |
| TD2 | 2 × 36 | anciennes cartes, certains titres (`I`, `A`, `C`) | oui |
| TD1 | 3 × 30 | cartes d'identité, titres de séjour (`I`, `A`, `C`) | oui |
| MRV-A | 2 × 44 | visas (`V`) | non |
| MRV-B | 2 × 36 | visas (`V`) | non |
| FRA ancienne CNI | 2 × 36 | CNI française avant 2021 (commence par `IDFRA`) | format national |

Positions (1 = premier caractère ; « cc » = chiffre de contrôle) — ICAO 9303 parties 4, 5, 6 et 7 :

**TD3**
- L1 : 1–2 type · 3–5 État émetteur · 6–44 nom (`NOM<<PRENOM<PRENOM2`)
- L2 : 1–9 n° document · 10 cc · 11–13 nationalité · 14–19 naissance `AAMMJJ` · 20 cc · 21 sexe · 22–27 expiration · 28 cc · 29–42 données facultatives (n° personnel) · 43 cc des données facultatives (`<` ou `0` si vides) · 44 cc composite sur L2 1–10, 14–20, 22–43

**TD1**
- L1 : 1–2 type · 3–5 État émetteur · 6–14 n° document · 15 cc · 16–30 données facultatives
- L2 : 1–6 naissance · 7 cc · 8 sexe · 9–14 expiration · 15 cc · 16–18 nationalité · 19–29 données facultatives · 30 cc composite sur L1 6–30, L2 1–7, 9–15, 19–29
- L3 : 1–30 nom
- **N° de document de plus de 9 caractères** : les 9 premiers en 6–14, `<` en 15, la suite puis son chiffre de contrôle au début des données facultatives (16…). Même principe en TD2 dans les données facultatives.

**TD2**
- L1 : 1–2 type · 3–5 État émetteur · 6–36 nom
- L2 : 1–9 n° document · 10 cc · 11–13 nationalité · 14–19 naissance · 20 cc · 21 sexe · 22–27 expiration · 28 cc · 29–35 données facultatives · 36 cc composite sur L2 1–10, 14–20, 22–35

**MRV-A / MRV-B** : comme TD3 / TD2 pour la ligne 2 jusqu'à la position 28, puis données facultatives jusqu'au bout, **sans** somme composite.

**Ancienne CNI française (2 × 36, format national)** — vérifié sur le spécimen ci-dessous, à confirmer sur d'autres spécimens :
- L1 : 1–2 `ID` · 3–5 `FRA` · 6–30 nom · 31–36 code administratif (département + bureau)
- L2 : 1–12 n° de carte (`AAMM` + département + numéro) · 13 cc · 14–27 prénoms · 28–33 naissance · 34 cc · 35 sexe · 36 cc composite sur L1 entière + L2 1–35

Distinguer TD3 de MRV-A et TD2 de MRV-B par le premier caractère (`P` / `V`), et l'ancienne CNI française par `IDFRA` + la structure de la ligne 2.

### E.5 Sommes de contrôle et angles morts

Algorithme : `0–9` → 0–9, `A–Z` → 10–35, `<` → 0 ; poids 7, 3, 1 répétés ; somme modulo 10.

**Angles morts** : deux caractères dont les valeurs sont égales modulo 10 donnent la même somme de contrôle à n'importe quelle position (les poids 7, 3 et 1 sont premiers avec 10). Classes indiscernables :

| Classe | Caractères |
|---|---|
| 0 | `0 A K U <` |
| 1 | `1 B L V` |
| 2 | `2 C M W` |
| 3 | `3 D N X` |
| 4 | `4 E O Y` |
| 5 | `5 F P Z` |
| 6 | `6 G Q` |
| 7 | `7 H R` |
| 8 | `8 I S` |
| 9 | `9 J T` |

Conséquences pour les confusions OCR courantes :
- **détectées** par la somme : `O↔0`, `I↔1`, `Z↔2`, `B↔8`, `D↔0`, `Q↔0`, `S↔5` ;
- **invisibles** pour la somme : `S↔8`, `G↔6`, `B↔1`, `Z↔5`, `U↔0`, `K↔<`.

Règle : une correction qui reste à l'intérieur d'une même classe **n'est pas prouvée** par la somme de contrôle. Elle doit être confirmée par une autre source (vote unanime des moteurs, contrainte de type de champ, motif de numéro propre au pays, zone visuelle), sinon le champ passe « À vérifier ».

### E.6 Réparation guidée

1. **Contraintes de type par position** : numérique (dates, chiffres de contrôle), alphabétique (codes pays, noms), alphanumérique (n° de document, données facultatives), sexe ∈ {`M`, `F`, `X`, `<`}.
2. **Projection de type** (sûre, car le type est imposé par la norme) :
   - en position numérique : `O Q D U → 0`, `I L → 1`, `Z → 2`, `S → 5`, `G → 6`, `T → 7`, `B → 8` ;
   - en position alphabétique : `0 → O`, `1 → I`, `2 → Z`, `5 → S`, `6 → G`, `8 → B`.
3. **Recherche bornée** pour chaque champ dont le chiffre de contrôle est faux : variantes construites à partir des alternatives (distribution du vote, candidats Vision, matrice de confusion OCR-B), au plus 2 positions modifiées. Garder les variantes qui valident le chiffre du champ **et** la somme composite.
   - exactement une variante → acceptée, champ marqué `repaired` ;
   - plusieurs → la plus probable est proposée, champ « À vérifier », les autres en alternatives ;
   - aucune → « À vérifier », MRZ invalide.
4. **Contrôles sémantiques** : date réelle (mois 01–12, jour valide pour le mois) ; code pays dans la liste ICAO (ISO 3166-1 alpha-3 + `reference/icao_special_codes.yaml`) ; expiration postérieure à la naissance ; âge ≤ 120 ans.
5. **Siècle** : naissance → `20AA`, sauf si cette date tombe après aujourd'hui, auquel cas `19AA` ; expiration → `20AA`. La zone visuelle (année sur 4 chiffres) a le dernier mot. Si la zone visuelle ne confirme pas le siècle et que l'âge calculé est de moins de 2 ans ou de plus de 98 ans, la date de naissance passe « À vérifier ».
6. **Motifs de numéro par pays** (`reference/doc_number_patterns.yaml`, à créer et enrichir) : ex. le passeport français suit `\d{2}[A-Z]{2}\d{5}`. Un motif départage les angles morts du n° de document (`S/8`, `G/6`…).

La matrice de confusion OCR-B démarre à la main (`0/O/D/Q/U`, `1/I/L`, `2/Z`, `5/S`, `6/G`, `8/B`, `</K`) puis est **apprise** à partir des corrections humaines (statistiques de l'écran de vérification).

### E.7 Noms

- Champ nom : séparer au premier `<<` → nom ; le reste → prénoms ; `<` simple → espace ; supprimer le remplissage final.
- **Troncature** : si le champ nom est rempli jusqu'au dernier caractère sans `<` final, le nom est peut-être tronqué. La zone visuelle fait alors foi ; la comparaison MRZ ↔ zone visuelle se fait par préfixe.
- **Translittération** : la MRZ n'a pas d'accents. Comparer en appliquant la table ICAO 9303 partie 3 (§ 6) : certains caractères ont plusieurs transcriptions admises (ex. `Ä` → `A` ou `AE`, `Ö` → `O` ou `OE`, `Ü` → `U` ou `UE`, `ß` → `SS`, `Ø` → `OE`, `Å` → `AA`). Générer toutes les variantes et accepter une correspondance avec l'une d'elles. Pour les écritures non latines (cyrillique, arabe…), comparer la MRZ à la ligne latine de la zone visuelle quand elle existe, sinon à `anyascii(texte)` avec une similarité plus tolérante.

### E.8 Vecteurs de test (spécimens publics)

```
TD3 (ICAO 9303, spécimen « UTO »)
P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<
L898902C36UTO7408122F1204159ZE184226B<<<<<10
→ n° L898902C3, nationalité UTO, naissance 1974-08-12, sexe F, expiration 2012-04-15,
  n° personnel ZE184226B, toutes les sommes valides

TD1 (ICAO 9303, spécimen « UTO »)
I<UTOD231458907<<<<<<<<<<<<<<<
7408122F1204159UTO<<<<<<<<<<<6
ERIKSSON<<ANNA<MARIA<<<<<<<<<<
→ n° D23145890, naissance 1974-08-12, sexe F, expiration 2012-04-15, toutes les sommes valides

Ancienne CNI française (spécimen)
IDFRABERTHIER<<<<<<<<<<<<<<<<<<<<<<<
8806923102858CORINNE<<<<<<<6512068F6
→ n° 880692310285, prénoms CORINNE, naissance 1965-12-06, sexe F, toutes les sommes valides
```

Tests de réparation à écrire à partir de ces spécimens : remplacer `0` par `O` dans une date (doit être réparé et prouvé), remplacer `8` par `S` dans le n° de document (doit rester « À vérifier » sans autre preuve), tronquer une ligne d'un `<` (doit être réaligné).

---

## F — Classification

**Avec MRZ** :
- type de document par le code : `P…` → Passeport ; `V…` → Visa ; `I…`, `A…`, `C…` → Carte d'identité par défaut, avec une table de surcharge par pays (`reference/doc_codes.yaml`, à créer : certains pays utilisent ces codes pour des titres de séjour) ;
- pays émetteur (positions 3–5) et format de MRZ.

**Sans MRZ** :
- mots-clés sur l'OCR complet (« PASSPORT », « PASSEPORT », « PASAPORTE », « REISEPASS », « ПАСПОРТ », « 护照 », « 旅券 », « 여권 », « جواز سفر », « IDENTITY CARD », « CARTE NATIONALE D'IDENTITÉ », « DOCUMENTO NACIONAL DE IDENTIDAD », « PERSONALAUSWEIS », « CARTA D'IDENTITÀ »…) ;
- nom du pays dans plusieurs langues (Babel) ;
- format physique (étape C) ;
- type de code-barres (étape I).
→ « type probable », document toujours « À vérifier » tant que le banc d'essai ne justifie pas mieux pour ce modèle.

## G — OCR de la zone visuelle

- **Langues** : déduites du pays émetteur via `reference/country_scripts.yaml`, plus `en`, `fr` et `es` (les libellés des passeports sont en général traduits dans l'une de ces langues). Croiser avec les langues que Vision propose sur cette machine (`idvision languages`) ; le mapping se fait par préfixe de code (`fr` ↔ `fr-FR`).
- **Vision** : `level: accurate`, `language_correction: false` (la correction linguistique « corrige » les noms propres en mots du dictionnaire), `top_candidates: 3`.
- **PP-OCR** : modèle choisi selon l'écriture : PP-OCRv6 medium pour le latin, le chinois et le japonais ; PP-OCRv5 dédié pour cyrillique, arabe, grec, coréen, thaï, devanagari.
- **Fusion** : apparier les lignes des deux moteurs par recouvrement (IoU ≥ 0,5) → « emplacements » portant chacun 1 à 2 lectures + candidats. Une lecture identique par les deux moteurs est une preuve forte.
- Masquer la bande MRZ pour ne pas la relire comme du texte visuel.

## H — Extraction des champs

Quatre techniques, dans cet ordre de priorité.

**H.1 Recherche guidée par la MRZ (la plus fiable).** Pour chaque valeur connue par la MRZ (nom, prénoms, naissance, expiration, n° de document, nationalité), chercher dans les emplacements de la zone visuelle la meilleure correspondance après normalisation (majuscules, suppression de la ponctuation, translittération ICAO, `anyascii`). Similarité `rapidfuzz` (ratio et partial_ratio), seuil de départ 0,85 à calibrer.
- Trouvée → la valeur de la zone visuelle (avec accents) devient la valeur affichée, l'écriture d'origine va dans `value_native`, la MRZ reste la preuve, et l'emplacement donne le cadre affiché dans l'écran de vérification.
- Dates : analyser toutes les chaînes de type date de la zone visuelle (H.3) et retenir celle dont (A, M, J) égale la date MRZ. Elle donne le siècle et confirme la valeur.

**H.2 Ancrage sur les libellés** (pour les champs absents de la MRZ : lieu de naissance, date de délivrance, autorité, nom d'usage, adresse…). Dictionnaire `reference/viz_labels.yaml`. Repérer l'emplacement libellé (correspondance approximative, multilingue, tolérante aux formes « 4. Nom / Surname / Apellidos (1) »), puis prendre la valeur :
- juste **en dessous** (recouvrement horizontal ≥ 30 %, écart vertical < 2,5 × la hauteur de ligne), disposition la plus courante ;
- sinon **à droite** sur la même ligne ;
- en excluant les emplacements qui sont eux-mêmes des libellés.

**H.3 Détecteurs par type** :
- dates : `JJ MMM AAAA` y compris bilingue (`12 JAN/JAN 1990`, `12 AVR/APR 1990`), `JJ.MM.AAAA`, `JJ/MM/AAAA`, `JJ-MM-AAAA`, `JJ MM AAAA`, `AAAA.MM.JJ`, `AAAA年MM月DD日` ; mois multilingues dans `reference/months.yaml` ;
- sexe : `M`, `F`, `X`, `M/M`, `F/F`, `Ж`, `М`, `男`, `女` ;
- numéros : correspondance avec le n° MRZ ou avec un motif pays.

**H.4 Gabarits** pour les documents fréquents : `reference/templates/<PAYS>_<type>_<version>.yaml` avec, pour chaque champ, une zone relative (x, y, l, h en % du recadrage normalisé), la langue et le format de date. Utilisés en priorité quand le modèle de document est reconnu (pays + type + format MRZ + vérification de quelques libellés). On les crée **à partir des statistiques** : les 20 modèles les plus traités, puis ceux qui génèrent le plus de corrections.

**Champs particuliers** :
- date de délivrance : doit être ≤ expiration et ≥ naissance ; une durée de validité inhabituelle (autre que 5 ou 10 ans environ) produit une alerte, jamais une valeur inventée ;
- lieu de naissance : texte libre, gardé tel qu'imprimé (espaces normalisés) ;
- nom d'usage (CNI française) : libellés « Nom d'usage », « épouse », « ép. » ;
- adresse (verso de certaines cartes) : bloc multiligne sous le libellé.

## I — Codes-barres

- `idvision detect_barcodes` : PDF417, QR, DataMatrix, Aztec.
- Contenu stocké brut dans les artefacts. Analyseurs ajoutés au cas par cas quand un format est fréquent et documenté (ex. AAMVA pour les PDF417 des permis nord-américains).
- Un code-barres analysé est une source forte ; la vérification cryptographique d'une signature n'est pas prévue en v1.

## J — Appariement recto/verso (au niveau du lot)

Une carte TD1 porte la MRZ au verso et le nom, la photo et souvent le numéro au recto. Score d'appariement entre un recadrage avec MRZ et un recadrage sans MRZ de même format physique :

| Indice | Points |
|---|---|
| même page, deux recadrages côte à côte | + 0,6 |
| fichiers consécutifs dans l'ordre d'upload du même lot | + 0,3 |
| n° de document de la MRZ retrouvé dans l'OCR de l'autre face | + 0,5 |
| nom ou date de naissance de la MRZ retrouvé sur l'autre face | + 0,3 |

Apparier automatiquement si le score ≥ 0,8 et sans concurrent à moins de 0,2 ; sinon proposer l'association dans l'interface (« association suggérée »).

Déclenchement : quand tous les jobs du lot sont terminés et qu'aucun fichier n'a été ajouté depuis 2 minutes, ou par le bouton « Lancer l'appariement ». L'appariement est relancé si de nouveaux fichiers arrivent dans le lot. Ensuite, la MRZ d'une face confirme la zone visuelle de l'autre (étape H appliquée sur les deux faces).

## K — IA locale (option, désactivée par défaut)

Déclenchée seulement si : pas de MRZ, ou un champ obligatoire manque après H, ou un conflit MRZ ↔ zone visuelle reste non résolu.

- **PaddleOCR-VL 1.6** (0,9 Md de paramètres, 109 langues) : troisième moteur de transcription pour les écritures mal couvertes par Vision et PP-OCR.
- **Qwen3-VL-4B-Instruct** en 4 bits : extraction structurée (JSON) par consigne avec schéma, quand les libellés et gabarits échouent.

**Règle d'ancrage** (non négociable) : chaque valeur proposée par l'IA doit correspondre (similarité ≥ 0,9) à une lecture OCR existante ou à la MRZ. Sinon elle reste « À vérifier » avec la source « IA (non confirmée) ». Une valeur issue de l'IA seule n'est **jamais** validée automatiquement.

Exploitation : chargement paresseux, vérification de la mémoire libre avant chargement, délai maximal de 120 s par document, déchargement après 10 min d'inactivité.

## L — Consolidation, confiance et statut

**Par champ** : `value`, `value_native`, `sources`, `evidence`, `candidates`, `confidence`, `status`.

Confiance de départ (à recalibrer pour qu'elle corresponde à la probabilité réelle d'être juste, mesurée sur le banc d'essai) :

| Situation | Confiance |
|---|---|
| Champ MRZ, chiffre de contrôle valide sans réparation | 0,99 |
| Champ MRZ réparé hors angle mort, réparation unique | 0,97 |
| Champ MRZ réparé dans un angle mort, sans confirmation | 0,60 |
| Champ zone visuelle confirmé par la MRZ | 0,99 |
| Champ zone visuelle lu à l'identique par deux moteurs | 0,90 |
| Champ zone visuelle lu par un seul moteur (confiance ≥ 0,8) | 0,75 |
| Valeur d'IA non ancrée | 0,40 |

**Champs obligatoires** (passeport et carte d'identité) : nom, prénoms, date de naissance, sexe, nationalité, n° du document, date d'expiration, pays émetteur.

**Statut du document** :
- **Validé (auto)** si : MRZ présente et entièrement valide (champs + composite), aucun champ obligatoire sous le seuil (`IDR_AUTO_VALIDATE_THRESHOLD`, 0,95 au départ), aucun conflit MRZ ↔ zone visuelle sur le nom et la date de naissance, qualité sans alerte forte.
- **À vérifier** sinon, avec la liste des raisons (codes stables + libellé français).
- **Échec** si aucun document n'est trouvé ou si rien n'est lisible, avec la raison issue de l'étape B.

**Alertes** (n'empêchent pas la validation) : document expiré, titulaire mineur à la date d'analyse, expiration dans moins de 6 mois, qualité faible, motif de numéro inhabituel, doublon de document (même pays + n° + date de naissance, avec `duplicate_of_id`). Le statut « Doublon » est réservé au fichier identique (même SHA-256).
