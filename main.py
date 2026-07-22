"""
API de génération du Bulletin d'inscription APNI.

POST /generate-bulletin
Body JSON: {"fillout_answers": { ... }}
  -> le contenu EXACT de "2.answers" tel que sorti par le module Fillout
     dans Make (mêmes clés courtes 5av5, 6qjk, etc.) — envoyé tel quel,
     aucun retraitement necessaire côté Make.

Réponse: le PDF rempli, en binaire, Content-Type: application/pdf.
"""
import os

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any, Dict

from mapping_and_fill import build_field_values, get_signature_urls, fill_pdf

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.pdf")

app = FastAPI(title="APNI Bulletin Generator")


class GenerateRequest(BaseModel):
    fillout_answers: Dict[str, Any]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate-bulletin")
def generate_bulletin(req: GenerateRequest):
    with open(TEMPLATE_PATH, "rb") as f:
        template_bytes = f.read()

    field_values = build_field_values(req.fillout_answers)
    signature_urls = get_signature_urls(req.fillout_answers)
    pdf_bytes = fill_pdf(template_bytes, field_values, signature_urls)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="bulletin_apni.pdf"'},
    )
