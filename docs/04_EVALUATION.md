# 04 — Mesure du taux de réussite

« Le meilleur taux de réussite » ne se décrète pas : il se mesure, sur des documents représentatifs, à chaque modification. Ce banc d'essai est construit **dès la phase 1**.

## Définitions

| Indicateur | Définition |
|---|---|
| Champ correct | égal à la vérité terrain après normalisation (majuscules, espaces simples, dates ISO). Pour les noms, deux comptes : avec accents et sans accents. |
| Document entièrement correct | tous les champs obligatoires corrects |
| **Précision de l'auto-validation** | part des documents « Validé (auto) » entièrement corrects. **Indicateur n° 1** : une erreur validée en silence coûte bien plus cher qu'une vérification. |
| Taux d'automatisation | part des documents « Validé (auto) » |
| Taux d'échec | part des documents en « Échec », ventilée par raison |
| Exactitude par champ | par champ, par pays, par type de document, par format de MRZ |
| Temps | p50 et p95 par document, par étape ; mémoire maximale |

L'objectif est de **maximiser l'automatisation sous contrainte de précision** : on ne gagne jamais de l'automatisation en baissant la précision.

## Corpus de test

1. **`backend/tests/fixtures/`** (dans git) — uniquement des données publiques ou synthétiques :
   - spécimens publiés (exemples ICAO 9303, spécimens officiels diffusés par les États) ;
   - **MRZ synthétiques** générées par `scripts/gen_mrz.py` (champs aléatoires, sommes de contrôle valides), rendues en police OCR-B sur des fonds variés puis dégradées (flou, bruit, compression JPEG, perspective, reflet simulé, faible contraste). Plusieurs milliers d'images : c'est le banc de robustesse de l'étape E.
2. **PRADO** (registre public du Conseil de l'UE) : descriptions et images de spécimens de documents européens et de quelques pays tiers. Vérifier les conditions d'utilisation du site avant tout téléchargement et ne garder que ce qu'elles permettent.
3. **`data/golden/`** (hors git, disque chiffré) — **vraies pièces**, ajoutées depuis l'écran de vérification une fois validées par un humain (bouton « Ajouter au jeu de test », réservé aux admins, à n'activer qu'après accord du DPO, voir `05_SECURITE_RGPD.md`). Cible : 300 documents couvrant les 20 pays les plus fréquents + 50 documents « difficiles » (photos de travers, reflets, scans pâles, recto/verso séparés).

Format d'un cas :

```
golden/<uuid>/
  input/                 # 1 ou plusieurs fichiers tels que reçus
  truth.json
```

```json
{
  "documents": [{
    "doc_type": "passport",
    "issuing_state": "FRA",
    "mrz": ["P<FRA…", "…"],
    "fields": {
      "surname": "…", "given_names": "…", "sex": "F",
      "date_of_birth": "1990-01-31", "place_of_birth": "…",
      "nationality": "FRA", "document_number": "…",
      "date_of_issue": "2020-02-01", "date_of_expiry": "2030-01-31",
      "authority": "…"
    },
    "pages": [{"file": "input/scan.pdf", "page": 1}]
  }],
  "difficulty": ["glare", "perspective"],
  "notes": ""
}
```

## Banc d'essai

```bash
uv run idreader bench --set golden                    # jeu réel
uv run idreader bench --set synthetic --only-stage mrz
uv run idreader bench --set golden --compare last     # comparaison avec le dernier résultat
```

Produit `bench/results/<date>_<git-sha>_<set>.json` et un rapport Markdown :
- tableau des indicateurs ci-dessus, avec écart par rapport à la référence ;
- exactitude par champ, par pays, par type ;
- **liste des erreurs auto-validées** (doit être vide) avec lien vers l'artefact ;
- matrice de confusion des caractères MRZ (alimente la matrice de confusion de E.6) ;
- temps par étape et mémoire maximale.

Le rapport ne contient **jamais** de valeur personnelle du jeu réel : seulement des identifiants de cas, des noms de champs et des indicateurs.

## Règles de fusion

- Pas de fusion si la précision de l'auto-validation baisse, quelle que soit l'ampleur.
- Pas de fusion si le taux de documents entièrement corrects baisse de plus de 0,5 point.
- Toute modification d'un seuil ou d'un poids est accompagnée du rapport de banc qui la justifie.

## Objectifs de départ (à réviser après la phase 1)

| Indicateur | Objectif |
|---|---|
| MRZ lue et entièrement valide, passeports de qualité correcte | ≥ 98 % |
| Précision de l'auto-validation | ≥ 99,5 % |
| Automatisation, passeports | ≥ 85 % |
| Automatisation, cartes avec MRZ (recto/verso compris) | ≥ 70 % |
| Automatisation, documents sans MRZ | 0 % au départ (vérification systématique) |
| Temps p95 par document sans IA générative, Mac mini M1 | < 30 s |

## Calibration

- Les seuils et poids (vote, similarités, confiance) sont calibrés sur `golden`, jamais sur les seules données synthétiques.
- Calibrer la confiance pour qu'elle corresponde à la probabilité réelle d'être juste (courbe de fiabilité ; régression isotonique quand le jeu est assez grand).
- Le seuil d'auto-validation est choisi comme le plus bas qui garde la précision ≥ 99,5 % sur `golden`.

## Régression liée à macOS

Le moteur n°1 est fourni par le système : une mise à jour de macOS peut changer ses résultats sans aucune modification de notre code. Des développeurs ont signalé une forte baisse de qualité de l'OCR `accurate` de Vision après le passage d'iOS 26 à iOS 27 (lignes ignorées). Donc :
- noter la version de macOS dans chaque résultat de banc ;
- relancer le banc complet **après chaque mise à jour de macOS**, même mineure, avant de remettre le service en route ;
- c'est aussi pour cela que le pipeline croise trois moteurs : la défaillance d'un seul ne doit pas faire valider d'erreur.
