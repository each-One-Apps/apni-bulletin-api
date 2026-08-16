"""
API de génération du Bulletin d'inscription APNI.

POST /generate-bulletin
  Corps JSON : {"fillout_answers": { … }} — le contenu EXACT de `2.answers`
  tel que sorti du module Fillout dans Make (mêmes clés courtes 5av5, 6qjk…),
  envoyé tel quel, sans retraitement.

  Réponse par défaut (format=airtable), à destination du champ attachment :

      {"attachment": [{"url": "…", "filename": "bulletin_apni_NOM_Prenom.pdf"}],
       "filename": "…", "signatures_manquantes": []}

  ?format=pdf renvoie le PDF binaire — pour les tests et tout autre appelant.

GET /bulletin.pdf?d=<charge>&nom=<fichier>
  Régénère le bulletin depuis la charge encodée. C'est cette URL qu'Airtable
  télécharge.

POURQUOI UNE URL ET PAS LES OCTETS
Airtable va chercher ses pièces jointes à une URL : on ne peut pas lui pousser
du binaire. C'est ce qui a fait échouer ce scénario en 413 le 2026-07-22, sans
que personne ne le voie pendant trois semaines. Plutôt qu'héberger le PDF
quelque part — ce qui imposerait un stockage et un secret à un service qui n'en
a aucun — on renvoie une URL vers ce même service, portant la charge compressée.
Aucun état, aucune expiration, aucun ménage. Motif éprouvé en production sur
`ecf-livret-api` depuis le 2026-08-11.

Ce qu'on encode n'est pas les réponses Fillout brutes mais le RÉSULTAT du
mapping : c'est plus court, et le GET devient un pur rendu.
"""
import base64
import json
import logging
import os
import zlib
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from mapping_and_fill import build_field_values, fill_pdf, get_signature_urls, nom_fichier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("apni")

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.pdf")

# Au-delà, l'URL devient inexploitable par certains intermédiaires. Mieux vaut
# un refus explicite qu'une pièce jointe qui ne se télécharge pas.
# Mesuré sur un formulaire complet (44 champs + 2 signatures) : 1 339 caractères.
URL_MAX = 8000

app = FastAPI(title="APNI — bulletin d'inscription")


def _encoder(field_values, signature_urls):
    """Mapping -> chaîne transportable par URL.

    `sort_keys` et `separators` ne sont pas cosmétiques : ils rendent la charge
    identique pour un même formulaire, donc l'URL stable d'une soumission à
    l'autre.
    """
    charge = {
        "f": {fv["field_id"]: fv["value"] for fv in field_values},
        "s": signature_urls,
    }
    brut = json.dumps(charge, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    comprime = zlib.compress(brut.encode("utf-8"), 9)
    return base64.urlsafe_b64encode(comprime).decode("ascii").rstrip("=")


def _decoder(encode):
    rembourrage = "=" * (-len(encode) % 4)
    try:
        brut = zlib.decompress(base64.urlsafe_b64decode(encode + rembourrage)).decode("utf-8")
        charge = json.loads(brut)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"paramètre « d » illisible : {e}")
    if not isinstance(charge, dict):
        raise HTTPException(status_code=400, detail="paramètre « d » : objet attendu")
    valeurs = charge.get("f") or {}
    signatures = charge.get("s") or {}
    if not isinstance(valeurs, dict) or not isinstance(signatures, dict):
        raise HTTPException(status_code=400, detail="paramètre « d » : structure inattendue")
    return ([{"field_id": k, "value": v} for k, v in valeurs.items()], signatures)


def _base_url(request):
    """URL publique du service, en tenant compte du proxy de l'hébergeur.

    Sans `x-forwarded-host`, l'URL fabriquée derrière Vercel désigne l'hôte
    interne de la fonction — qu'Airtable ne sait pas joindre.
    """
    hote = request.headers.get("x-forwarded-host") or request.headers.get("host")
    protocole = request.headers.get("x-forwarded-proto") or request.url.scheme
    if not hote:
        return str(request.base_url).rstrip("/")
    return f"{protocole}://{hote}"


def _template():
    with open(TEMPLATE_PATH, "rb") as f:
        return f.read()


def _rendre(field_values, signature_urls):
    try:
        return fill_pdf(_template(), field_values, signature_urls)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rendu impossible")
        raise HTTPException(status_code=500, detail=f"rendu impossible : {e}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/bulletin.pdf")
def bulletin_pdf(d: str = "", nom: str = "bulletin_apni.pdf"):
    field_values, signature_urls = _decoder(d) if d else ([], {})
    pdf, manquantes = _rendre(field_values, signature_urls)
    entetes = {
        "Content-Disposition": f'inline; filename="{nom}"',
        "X-APNI-Signatures-Manquantes": ",".join(manquantes),
    }
    return Response(content=pdf, media_type="application/pdf", headers=entetes)


@app.post("/generate-bulletin")
async def generate_bulletin(request: Request, format: str = "airtable"):
    try:
        charge = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"corps JSON illisible : {e}")
    if not isinstance(charge, dict) or not isinstance(charge.get("fillout_answers"), dict):
        raise HTTPException(status_code=400, detail="« fillout_answers » attendu, en objet")

    answers = charge["fillout_answers"]
    field_values = build_field_values(answers)
    signature_urls = get_signature_urls(answers)
    nom = nom_fichier(answers)

    # On rend le PDF même en format=airtable, alors que seule l'URL est
    # renvoyée : c'est ce qui fait échouer l'appel AVANT l'écriture Airtable si
    # le rendu est impossible. Avec stopOnHttpError, Make n'écrit alors rien.
    pdf, manquantes = _rendre(field_values, signature_urls)

    if manquantes:
        logger.warning("%s — signatures manquantes : %s", nom, ", ".join(manquantes))
    logger.info("%s — %d champ(s), %d octets", nom, len(field_values), len(pdf))

    entetes = {"X-APNI-Signatures-Manquantes": ",".join(manquantes)}

    if format == "pdf":
        entetes["Content-Disposition"] = f'attachment; filename="{nom}"'
        return Response(content=pdf, media_type="application/pdf", headers=entetes)

    encode = _encoder(field_values, signature_urls)
    if len(encode) > URL_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"réponses trop volumineuses pour être transportées par URL "
                   f"({len(encode)} caractères une fois compressées, maximum {URL_MAX})",
        )
    url = "{}/bulletin.pdf?{}".format(
        _base_url(request), urlencode({"d": encode, "nom": nom}, quote_via=quote)
    )
    return JSONResponse(
        content={
            "attachment": [{"url": url, "filename": nom}],
            "filename": nom,
            "signatures_manquantes": manquantes,
        },
        headers=entetes,
    )
