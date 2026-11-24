# 01 — Spécification fonctionnelle

## Objectif

Transformer des fichiers de pièces d'identité (PDF, JPG, PNG) en lignes de tableau fiables, avec le **maximum de documents validés automatiquement** et **zéro erreur validée en silence**. Ce qui n'est pas prouvé part en vérification humaine, et cette vérification doit prendre quelques secondes par document.

## Utilisateurs et rôles

| Rôle | Droits |
|---|---|
| Opérateur | Créer des lots, déposer des fichiers, consulter, vérifier, corriger, exporter |
| Admin | Tout ce que fait l'opérateur + gestion des comptes, réglages (conservation, seuils, IA optionnelle), purge, statistiques, ajout au jeu de test |

## Documents pris en charge

| Priorité | Type | Remarque |
|---|---|---|
| 1 | Passeports de tous pays | Format ICAO TD3 (MRZ 2 × 44). Tous les passeports en circulation sont lisibles par machine. |
| 1 | Cartes d'identité avec MRZ | TD1 (3 × 30, ex. cartes UE depuis 2021), TD2 (2 × 36), ancienne CNI française (2 × 36, format national) |
| 2 | Cartes d'identité sans MRZ | Certaines cartes nationales n'ont pas de MRZ. Extraction par la zone visuelle seule, donc « À vérifier » tant que le banc d'essai ne prouve pas mieux. |
| Bonus | Titres de séjour, visas (MRV-A/B), documents de voyage (réfugiés, apatrides) | Gérés automatiquement grâce à la MRZ |

**Formats de fichier** : PDF (1 à N pages), JPG, PNG. En bonus : HEIC (photos d'iPhone), TIFF, WEBP, ZIP contenant ces formats.

**Cas réels à gérer dès le départ** :
- une page ou une image contient **plusieurs documents** (recto et verso côte à côte sur un scan A4, plusieurs cartes sur une page, un PDF de 50 passeports) ;
- un document est réparti sur **deux fichiers** (recto dans l'un, verso dans l'autre) ;
- photos prises au téléphone : perspective, reflet, flou, doigt sur le bord, fond chargé ;
- passeport scanné en double page : seule la page de données (photo + MRZ) compte.

## Parcours principal

1. **Créer un lot** (ex. « Salon X — novembre ») ou en choisir un existant.
2. **Glisser-déposer** les fichiers (plusieurs à la fois). Chaque fichier affiche sa progression en direct.
3. Le **tableau se remplit** au fil de l'analyse.
4. Filtre **« À vérifier »** → écran de vérification → valider ou corriger → document suivant.
5. **Export** Excel ou CSV du lot, de la sélection ou du filtre courant.

## Le tableau (écran principal)

Colonnes par défaut (masquables et réordonnables, préférences mémorisées par utilisateur) :

| Colonne | Source principale | Remarque |
|---|---|---|
| Statut | moteur | pastille colorée + raisons au survol |
| Type | MRZ ou classification | Passeport, Carte d'identité, Titre de séjour, Visa, Autre |
| Pays émetteur | MRZ | code ICAO + nom du pays |
| Nom | zone visuelle confirmée par la MRZ | avec accents si la zone visuelle les donne |
| Prénoms | idem | |
| Nom (écriture d'origine) | zone visuelle | cyrillique, arabe, chinois… si présent |
| Nom d'usage | zone visuelle | ex. CNI française « Nom d'usage » |
| Sexe | MRZ | M, F, X |
| Date de naissance | MRZ (+ zone visuelle pour le siècle) | |
| Lieu de naissance | zone visuelle | texte tel qu'imprimé |
| Nationalité | MRZ | |
| N° du document | MRZ | |
| Date de délivrance | zone visuelle | absente de la MRZ |
| Date d'expiration | MRZ | |
| Autorité de délivrance | zone visuelle | |
| N° personnel | MRZ (données facultatives) ou zone visuelle | selon les pays |
| MRZ | moteur | Valide / Invalide / Absente |
| Confiance | moteur | 0–100 |
| Alertes | moteur | Expiré, Mineur, Qualité faible, Doublon |
| Fichier source | ingestion | nom d'origine + page |
| Lot | | |
| Analysé le | | |

Fonctions : tri, filtres par colonne, recherche globale, sélection multiple, **édition en ligne** (journalisée, et le document passe en « Validé (manuel) »), aperçu du recadrage au survol de la ligne, ouverture de l'écran de vérification au clic.

## Statuts d'un document

| Statut | Signification |
|---|---|
| En attente / En cours | dans la file d'analyse (avec l'étape en cours) |
| Validé (auto) | toutes les valeurs obligatoires sont prouvées (voir `03_PIPELINE_ANALYSE.md`, étape L) |
| À vérifier | au moins un doute ; la liste des raisons est affichée (ex. « somme de contrôle du n° de document réparée sans confirmation », « nom MRZ ≠ nom zone visuelle », « pas de MRZ ») |
| Validé (manuel) | vérifié ou corrigé par un humain |
| Échec | raison explicite : aucun document détecté, image trop floue, reflet sur la MRZ, document coupé, résolution insuffisante, pas une pièce d'identité, format non pris en charge |
| Doublon | fichier identique (même empreinte SHA-256) déjà présent dans le lot. Un même **document** (pays + n° + date de naissance) déjà présent n'est pas un statut mais une **alerte** « Doublon », avec un lien vers l'original (`duplicate_of_id`) |

## Écran de vérification

**À gauche — l'image** : recadrage redressé, zoom et déplacement, rotation par 90°, bascule recto/verso, accès à la page d'origine. Un cadre coloré entoure chaque champ détecté ; survoler un champ du formulaire met son cadre en surbrillance.

**À droite — le formulaire** : pour chaque champ, la valeur, un badge de source (MRZ, OCR, code-barres, IA, humain), la confiance, la raison du doute, et un menu des **valeurs alternatives** proposées par les moteurs (souvent la bonne valeur est déjà dans la liste).

**La MRZ brute** est affichée en police à chasse fixe, chaque somme de contrôle colorée en vert ou en rouge, les caractères réparés soulignés.

**Raccourcis clavier** : Tab / Maj+Tab champ suivant/précédent, ↑/↓ choisir une alternative, Entrée valider et passer au document suivant, Échap annuler la saisie, R rotation, P associer recto/verso.

**Actions** : Valider, Rejeter (pas une pièce d'identité), Relancer l'analyse, Associer / Dissocier recto-verso, Supprimer, Ajouter au jeu de test (admin).

## Exports

- **XLSX** (openpyxl) : vraies cellules date, colonnes figées, filtre automatique.
- **CSV** : UTF-8 avec BOM, séparateur `;` (ouverture directe dans Excel en français), dates `JJ/MM/AAAA`.
- Colonnes exportées = colonnes visibles, avec option « inclure statut et alertes ».
- Périmètre : lot, sélection ou filtre courant.
- Chaque export est journalisé (qui, quand, nombre de lignes).

## Administration

- Comptes : création, désactivation, réinitialisation du mot de passe, rôle.
- Réglages : durée de conservation des images et des données, seuil d'auto-validation, activation de l'IA locale optionnelle, colonnes extraites (minimisation RGPD).
- Statistiques : taux d'auto-validation, taux d'échec par raison, temps moyen par document, répartition par pays et type, champs les plus corrigés.
- Santé : version de macOS, langues Vision disponibles, modèles chargés, file d'attente, espace disque, mémoire.
- Purge : par lot ou automatique selon la durée de conservation.

## Hors périmètre

- Vérification d'authenticité ou détection de fraude (hologrammes, UV, microtextes). On signale seulement les **incohérences** (somme de contrôle fausse, dates impossibles, MRZ ≠ zone visuelle).
- Lecture de la puce NFC/RFID.
- Accès depuis Internet.
- Reconnaissance ou comparaison de visages.

## Exigences non fonctionnelles

| Exigence | Valeur |
|---|---|
| Volume | 500 à 5 000 documents par mois, pics de plusieurs centaines par lot |
| Temps de traitement | objectif p95 < 30 s par document sans IA générative (à mesurer sur le Mac mini) ; traitement en arrière-plan |
| Accès | réseau local de l'entreprise, navigateur récent |
| Interface | en français, utilisable au clavier |
