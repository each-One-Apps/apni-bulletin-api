# API de génération du Bulletin d'inscription APNI (v2 — mapping intégré)

Cette version fait TOUT en un seul appel : elle prend les réponses Fillout
brutes, fait le mapping vers les champs du PDF, gère les champs "comb"
(cases individuelles), coche les cases à cocher, sélectionne les radios,
et incruste les deux signatures téléchargées automatiquement.

## Déploiement (Render.com, gratuit pour commencer)

1. Créer un compte sur https://render.com
2. Pousser ce dossier sur un repo Git (GitHub/GitLab)
3. Render : "New +" → "Web Service" → connecter le repo (Docker auto-détecté)
4. Récupérer l'URL fournie, ex. https://apni-bulletin.onrender.com

## Utilisation

POST /generate-bulletin
```json
{ "fillout_answers": { "5av5": "...", "6qjk": "Camille", ... } }
```
= exactement le contenu de `2.answers` dans Make, envoyé tel quel.

Réponse : le PDF rempli, en binaire (Content-Type: application/pdf).

## Dans Make (scénario simplifié — 2 modules seulement)

1. **Fillout** (trigger, déjà en place)
2. **HTTP → Make a request**
   - URL : `https://votre-app.onrender.com/generate-bulletin`
   - Method : POST
   - Body type : raw / JSON
   - Body : `{ "fillout_answers": {{2.answers}} }`
   - Parse response : binaire (laisser tel quel)
3. Le corps de la réponse (PDF binaire) se branche directement dans
   Airtable "Upload Attachment", Google Drive "Upload a File", ou Gmail.

Plus besoin du module Google Drive de téléchargement ni du module Code —
le template est embarqué dans l'image Docker.

## Mettre à jour le template

Si le PDF APNI change de mise en page, remplacer `template.pdf` dans ce
dossier et redéployer (Render redéploie automatiquement au push Git).

## Limites connues

- Les champs signature ne sont pas de vraies signatures électroniques :
  l'image envoyée par Fillout est simplement incrustée visuellement dans
  l'encadré prévu.
- Le mapping (fichier `mapping_and_fill.py`) est basé sur les clés de
  question Fillout actuelles (5av5, 6qjk, etc.). Si le formulaire Fillout
  est modifié (nouvelle question, question supprimée), il faut ajuster
  les dictionnaires `SIMPLE_FIELD_MAP`, `DATE_FIELD_MAP`, etc. en haut du
  fichier.
