# IDReader — dossier de cadrage

Site interne d'analyse de passeports et de cartes d'identité du monde entier (PDF, JPG, PNG) → tableau éditable et exportable. 100 % local sur un Mac mini M1.

## Utilisation avec Claude Code

1. Créer un dépôt vide et y copier ce dossier tel quel (`CLAUDE.md` à la racine).
2. Ouvrir Claude Code dans le dépôt, **sur le Mac mini** (ou un Mac Apple Silicon) : le helper Swift et Apple Vision ne se testent que sur macOS.
3. Coller la consigne de démarrage de la phase 0 (fin de `docs/07_ROADMAP.md`).
4. Avancer phase par phase ; ne passer à la suivante qu'une fois ses critères d'acceptation atteints.

## Stratégie en bref

- **MRZ d'abord** : tous les passeports et la plupart des cartes récentes ont une MRZ normalisée avec sommes de contrôle. Lue par 3 moteurs OCR, votée caractère par caractère, réparée seulement quand la somme de contrôle le prouve.
- **Zone visuelle ensuite** : lue par 2 moteurs (Apple Vision sur le Neural Engine + PP-OCR), recoupée avec la MRZ pour récupérer accents, écriture d'origine, lieu et date de délivrance.
- **Jamais d'erreur validée en silence** : ce qui n'est pas prouvé part en vérification humaine rapide (image + champs + alternatives, au clavier).
- **IA générative locale en option** seulement, après mesure, et sans jamais valider seule une valeur.
- **Tout se mesure** avec un banc d'essai dès la phase 1.

## Contenu

- `CLAUDE.md` — instructions permanentes pour Claude Code
- `docs/` — spec fonctionnelle, architecture, pipeline, évaluation, sécurité/RGPD, déploiement, roadmap
- `reference/` — libellés multilingues, mois, codes ICAO spéciaux, langues par pays (graines à enrichir)

## Points à trancher avant la mise en production

- RAM du Mac mini (8 ou 16 Go) et version de macOS → `deploy/MACHINE.md`.
- Cadre RGPD validé par le DPO (finalité, base légale, durées de conservation, jeu de test réel).
- Qui déverrouille le Mac après une coupure de courant (FileVault).
