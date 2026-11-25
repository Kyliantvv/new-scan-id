# Banc synthétique — étape E (MRZ) — 2026-11-25

- Commit : `f453f2d-dirty` · graine 2026 · 300 cas · date de référence 2026-11-25
- Plate-forme : macOS-27.2.1-arm64-arm-64bit · Python 3.12.14 · macOS 27.2.1
- **Moteurs actifs : vision, tesseract, ppocr**
- Tesseract : tesseract 5.5.4
- PP-OCR : PP-OCRv6 small rec (MRZ) ; medium det + rec (zone visuelle), ONNX
- Comparaison : `f453f2d` du 2026-11-25 (écart entre parenthèses)

## Indicateurs

| Indicateur | Valeur |
|---|---|
| MRZ localisée (%) | 100,0 (+0,0) |
| MRZ décodée (%) | 99,0 (+1,0) |
| **MRZ entièrement correcte (%)** | 81,7 (+2,0) |
| MRZ correcte avant réparation (%) | 36,3 (+7,3) |
| Sommes de contrôle toutes valides (%) | 92,0 (-0,3) |
| Automatisation, MRZ seule (%) | 0,0 (+0,0) |
| **Précision de l'auto-validation (%)** | — |
| Temps p50 par cas (ms) | 2201,5 (+940,8) |
| Temps p95 par cas (ms) | 3677,9 (+1864,5) |
| Erreurs auto-validées (doit être 0) | 0 |

L'automatisation est une approximation de l'étape L (phase 1b) à partir de la MRZ seule : toutes les sommes valides, aucun motif « À vérifier », champs obligatoires `valid` ou `repaired` avec une confiance ≥ 0,95.

## Par qualité d'image

| | Cas | Localisée | Correcte | Sommes valides | Auto | Précision auto | p50 ms |
|---|---|---|---|---|---|---|---|
| good | 100 | 100,0 | 85,0 | 93,0 | 0,0 | — | 2376,4 |
| medium | 100 | 100,0 | 86,0 | 93,0 | 0,0 | — | 2198,3 |
| poor | 100 | 100,0 | 74,0 | 90,0 | 0,0 | — | 2069,8 |

## Par format

| | Cas | Localisée | Correcte | Sommes valides | Auto | Précision auto | p50 ms |
|---|---|---|---|---|---|---|---|
| FRA-ID | 26 | 100,0 | 92,3 | 96,2 | 0,0 | — | 1843,3 |
| MRV-A | 18 | 100,0 | 83,3 | 94,4 | 0,0 | — | 2113,9 |
| MRV-B | 12 | 100,0 | 100,0 | 100,0 | 0,0 | — | 1881,9 |
| TD1 | 86 | 100,0 | 73,3 | 88,4 | 0,0 | — | 2389,6 |
| TD2 | 29 | 100,0 | 96,6 | 100,0 | 0,0 | — | 1889,6 |
| TD3 | 129 | 100,0 | 79,8 | 90,7 | 0,0 | — | 2336,4 |

## Exactitude par champ (%)

| Champ | Exact |
|---|---|
| surname | 98,0 |
| given_names | 98,7 |
| date_of_birth | 96,0 |
| sex | 96,0 |
| nationality | 93,8 |
| document_number | 89,7 |
| date_of_expiry | 96,0 |
| issuing_state | 98,0 |
| personal_number | 90,7 |

## Échecs et motifs « À vérifier »

- échec `MRZ_UNREADABLE` : 3
- motif `MRZ_BLIND_SPOT` : 145
- motif `MRZ_REPAIR_AMBIGUOUS` : 39
- motif `MRZ_COMPOSITE_FAILED` : 19
- motif `MRZ_UNKNOWN_COUNTRY` : 18
- motif `MRZ_CHECK_FAILED` : 18
- motif `MRZ_INVALID_DATE` : 9
- motif `MRZ_INVALID_SEX` : 9

## Confusions de caractères (vérité → lu, avant réparation)

| Vérité | Lu | N |
|---|---|---|
| `O` | `0` | 263 |
| `I` | `1` | 25 |
| `0` | `O` | 15 |
| `P` | `0` | 5 |
| `O` | `L` | 5 |
| `6` | `0` | 5 |
| `P` | `<` | 4 |
| `O` | `<` | 4 |
| `8` | `0` | 4 |
| `3` | `0` | 4 |
| `0` | `6` | 4 |
| `Z` | `2` | 4 |
| `L` | `<` | 3 |
| `0` | `8` | 3 |
| `8` | `1` | 3 |
| `7` | `1` | 3 |
| `6` | `2` | 3 |
| `2` | `0` | 3 |
| `F` | `3` | 3 |
| `4` | `<` | 3 |
| `<` | `8` | 3 |
| `3` | `6` | 3 |
| `0` | `4` | 3 |
| `1` | `2` | 3 |
| `0` | `3` | 3 |
| `G` | `B` | 2 |
| `T` | `<` | 2 |
| `D` | `Z` | 2 |
| `Z` | `A` | 2 |
| `A` | `<` | 2 |

## Temps par étape (p50, ms)

- locate : 20,4
- read : 2178,2
- decode : 2,5
- mémoire maximale du processus : 244,5 Mo
