# API de génération du Bulletin d'inscription APNI

Reçoit les réponses brutes d'un formulaire Fillout, remplit le PDF officiel du
bulletin, et renvoie de quoi le déposer dans une pièce jointe Airtable.

Appelé par le scénario Make **6643033** — `Generate APNI French Course
Subscription File`.

## ⚠️ Ce dépôt est public temporairement

Le plan Vercel Hobby refuse de relier un dépôt **privé d'organisation** : pas de
déploiement automatique sur push. Le dépôt a donc été publié pour avancer, et
sera **repassé en privé au passage en plan Pro**, prévu.

En conséquence : **aucun secret, aucun identifiant interne, aucune donnée
personnelle réelle** dans ce dépôt. Le service ne détient aucune clé — ni
Airtable, ni rien d'autre — et c'est ce qui rend la publication tenable. Les
jeux de test utilisent des valeurs fictives de même forme que les vraies.

## Contrat

### `POST /generate-bulletin`

```json
{ "fillout_answers": { "5av5": "…", "6qjk": "Sofia", … } }
```

Soit exactement le contenu de `{{2.answers}}` dans Make, envoyé tel quel : le
service fait lui-même toute la correspondance vers les champs du PDF.

Réponse par défaut (`?format=airtable`) :

```json
{
  "attachment": [
    {"url": "https://…/bulletin.pdf?d=…&nom=…",
     "filename": "bulletin_apni_MARTINEZ_Sofia.pdf"}
  ],
  "filename": "bulletin_apni_MARTINEZ_Sofia.pdf",
  "signatures_manquantes": []
}
```

`?format=pdf` renvoie le PDF binaire.

| Code | Quand |
|---|---|
| `400` | corps JSON illisible, ou `fillout_answers` absent |
| `422` | réponses trop volumineuses pour tenir dans l'URL (plafond 8 000 caractères) |
| `500` | rendu impossible |

En-tête `X-APNI-Signatures-Manquantes` sur toutes les réponses réussies. Une
signature injoignable **n'interrompt pas** la génération : le bulletin sort sans
elle, mais l'absence est signalée là et dans les logs.

### `GET /bulletin.pdf?d=<charge>&nom=<fichier>`

Régénère le bulletin depuis la charge encodée. **C'est cette URL qu'Airtable
télécharge.**

## Pourquoi une URL et pas les octets du PDF

Airtable va chercher ses pièces jointes à une URL : on ne peut pas lui pousser
du binaire. Le module attend `[{url, filename}]`.

C'est ce qui a fait échouer le scénario en `413` le 2026-07-22 — sans que
personne ne s'en aperçoive pendant trois semaines, le champ
`application_apni_subscription_file_attachment` étant resté vide sur la totalité
des fiches.

Plutôt qu'héberger le PDF quelque part — ce qui imposerait un stockage et un
secret à un service qui n'en a aucun — le POST renvoie une **URL vers ce même
service**, portant le résultat du mapping compressé (`zlib` + `base64url`). Le
rendu étant déterministe, le GET reproduit le fichier à l'octet près. Aucun
état, aucune expiration, aucun ménage.

Ce qui est encodé n'est pas les réponses Fillout brutes mais le **résultat du
mapping** : c'est plus court, et le GET devient un pur rendu. Mesuré sur un
formulaire complet : **1 339 caractères** d'URL, sur un plafond de 8 000.

Même motif que le service frère `ecf-livret-api`, en production depuis le
2026-08-11.

## Trois pièges du template, traités

**Le PDF a un AcroForm.** Contrairement au livret ECF, il n'y a aucune
coordonnée à mesurer : chaque champ porte son rectangle, le code écrit dedans.
Seuls les champs « comb » (découpés en cases : n° de sécurité sociale, dates)
sont peints à la main, la largeur de case étant déduite du rectangle.

**Cinq champs sont définis sur un parent** — « Organisme de formation », « Code
du module », « Lieu de formation » et les deux « Civilité ». Leur widget n'a pas
de `/T`, donc `update_page_form_field_values` ne les trouvait pas et ne les
remplissait pas : **ces cinq champs sont restés vides sur tous les bulletins
jusqu'au 2026-08-16**. On écrit désormais `/V` sur le champ porteur, et `/AS`
sur le bon widget pour les boutons — `/V` seul laisse la case visuellement vide
alors que le formulaire la dit cochée.

**Le calque ne doit pas être fusionné dans la page.** `page.merge_page()`
recombine les flux de contenu, donc décompresse celui de la page et le réécrit
tel quel : les pages 3 et 4 pèsent 2,1 Mo décompressées chacune, et le bulletin
sortait à **5,4 Mo au lieu de 2,0** — au-dessus de la limite de réponse de
4,5 Mo d'une fonction Vercel. Le calque est donc posé en Form XObject appelé
depuis `/Contents`, sans toucher au flux d'origine.

## Déploiement

**Vercel**, équipe `each-one`, déploiement automatique sur push vers `main`.
Vercel détecte `main.py` exposant `app` et installe `requirements.txt` : rien à
configurer. Le `Dockerfile` est conservé pour Render ou Dokploy, et exclu du
paquet Vercel par `.vercelignore`.

## Développement

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Mettre à jour le template

Remplacer `template.pdf` et relancer les tests : ceux de `tests/test_rendu.py`
vérifient que les champs attendus existent toujours et que le poids reste sous
la limite Vercel. Si le formulaire Fillout change (question ajoutée ou
supprimée), ajuster les dictionnaires en haut de `mapping_and_fill.py` — les
clés y sont celles de Fillout (`5av5`, `6qjk`…).
