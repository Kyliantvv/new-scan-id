# Banc documents — pipeline complet — 2026-11-25

- Commit : `677edbf-dirty` · graine 2026 · 60 documents UTO synthétiques · date de référence 2026-11-25
- Plate-forme : macOS-27.2.1-arm64-arm-64bit · Python 3.12.14
- **Moteurs actifs : ppocr, tesseract, vision**
- Spécimens UTO : motif SPECIMEN_DOCUMENT levé pour la mesure. Recto et verso réunis par l'appariement réel (étape J) sur tout le jeu, fichiers dans l'ordre ; une carte non appariée est jugée sur son document avec MRZ.
- Comparaison : `f453f2d-dirty` du 2026-11-25

## Indicateurs

| Indicateur | Valeur |
|---|---|
| Documents détectés au bon nombre (%) | 100,0 (+0,0) |
| **Champs obligatoires tous justes (%)** | 41,7 (+1,7) |
| Noms accentués justes, accents compris (%) | 52,7 (+1,1) |
| Noms justes sans tenir compte des accents (%) | 75,0 (+0,0) |
| Cartes : recto et verso appariés automatiquement (%) | 81,2 (+0,0) |
| Un seul document par pièce (%) | 90,0 (+0,0) |
| Automatisation (%) | 0,0 (+0,0) |
| **Précision de l'auto-validation (%)** | — |
| Temps p50 par document (ms) | 13876,9 (+1354,7) |
| Temps p95 par document (ms) | 26882,5 (+7127,0) |
| Erreurs auto-validées (doit être 0) | 0 |
| Fausses paires automatiques (doit être 0) | 0 |

## Exactitude par champ

| Champ | Juste (%) |
|---|---|
| Nom | 56,7 |
| Prénoms | 63,3 |
| Nom d'usage | 62,5 |
| Sexe | 80,0 |
| Date de naissance | 86,7 |
| Lieu de naissance | 66,7 |
| Nationalité | 80,0 |
| N° de document | 80,0 |
| Date de délivrance | 73,3 |
| Date d'expiration | 86,7 |
| Autorité | 68,3 |
| État émetteur | 86,7 |

## Par groupe

| Groupe | Cas | Obligatoires justes (%) | Accents (%) | Auto (%) |
|---|---|---|---|---|
| kind=id_card | 32 | 40,6 | 45,8 | 0,0 |
| kind=passport | 28 | 42,9 | 60,5 | 0,0 |
| quality=good | 20 | 70,0 | 78,1 | 0,0 |
| quality=medium | 20 | 50,0 | 72,4 | 0,0 |
| quality=poor | 20 | 5,0 | 6,7 | 0,0 |
| layout=a4 | 7 | 14,3 | 20,0 | 0,0 |
| layout=separate | 53 | 45,3 | 56,8 | 0,0 |

## Statuts des recadrages

- failed : 2
- to_review : 64

## Motifs « À vérifier » les plus fréquents

- `FIELD_UNVERIFIED` : 50
- `VIZ_ACCENTS_UNCONFIRMED` : 24
- `MRZ_NOT_FOUND` : 12
- `PROBABLE_TYPE` : 12
- `FIELD_MISSING` : 11
- `MRZ_BLIND_SPOT` : 8
- `MRZ_COMPOSITE_FAILED` : 4
- `MRZ_INVALID_SEX` : 4
- `MRZ_UNKNOWN_COUNTRY` : 4
- `VIZ_MRZ_DOB_CONFLICT` : 4
- `VIZ_MRZ_EXPIRY_CONFLICT` : 4
- `FIELD_LOW_CONFIDENCE` : 4
- `MRZ_REPAIR_AMBIGUOUS` : 2
- `TOO_BLURRY` : 2
- `QUALITY_GLARE_MRZ` : 1
