"""
Module Make "Code" (Python) — génération du Bulletin d'inscription APNI
à partir des réponses Fillout brutes.

Config du module Code dans Make :
- language: Python
- Advanced settings > Add dependencies : pypdf , reportlab , requests
- Input à mapper :
    - template_base64 : le PDF vierge en base64 (cf. note en bas de fichier)
    - fillout_answers : mappe directement `2.answers` (le bundle "Answers"
      sorti du module Fillout "Get Record From Fillout") — pas besoin de le
      retraiter avant, ce script fait tout le mapping lui-même.

Sortie :
    - pdf_base64 : le PDF rempli, encodé en base64
"""
import base64
import io

import requests
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, IndirectObject
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

COMB_BIT = 1 << 24  # bit 25 (1-indexed) = flag "Comb" d'un champ PDF


# ---------------------------------------------------------------------------
# 1) Mapping des clés Fillout -> field_id du PDF (+ transformations de valeur)
# ---------------------------------------------------------------------------

def ddmm_from_iso(date_str):
    """'1991-12-15' -> '15121991' (pour les champs comb à 8 cases)."""
    if not date_str:
        return ""
    y, m, d = date_str.split("-")[:3]
    d = d[:2]
    return f"{d}{m}{y}"


NIVEAU_MAP = [
    ("Non concerné", "/Choix1"),
    ("Niveau 2", "/Choix2"),
    ("Niveau 3 BIS", "/Choix3"),  # à tester avant "Niveau 3" (préfixe commun)
    ("Niveau 3", "/Choix4"),
    ("Niveau 4", "/Choix5"),
    ("Niveau 5", "/Choix6"),
    ("Niveau 6", "/Choix7"),
]


def niveau_choix(label):
    if not label:
        return None
    for prefix, choix in NIVEAU_MAP:
        if label.startswith(prefix):
            return choix
    return None


# Champs texte/nombre en correspondance directe (1 réponse Fillout -> 1 champ PDF)
SIMPLE_FIELD_MAP = {
    "6qjk": "Prénom",
    "2xYg": "Email",
    "kghY": "Ville",
    "bqs8": "Téléphone",
    "rGdi": "Nom de naissance AM",
    "mDbu": "Nom d’usage",
    "pQMu": "Adresse",
    "iaB3": "Numéro de sécurité sociale",       # comb, maxlen 15
    "nSr5": "Code postale",                      # comb, maxlen 5

    "hRqu": "PrénomPE",
    "afcR": "EmailPE",
    "cmg3": "VillePE",
    "aZUq": "Téléphone PE",
    "3nU2": "Nom de naissancePE",
    "4La3": "Nom d’usagePE",
    "ccFv": "AdressePE",
    "9zAa": "Numéro CR CESU Pajemploi ou Urssaf",  # comb, maxlen 11
    "wbaa": "Code postalePE",

    "aZiu": "Organisme de formation",
    "uLVH": "Adresse postale",
    "wDAp": "Intitulé du module",
    "21Bn": "Code du module",
    "1s6s": "Lieu de formation",
    "sqyF": "Nombre d’heures du module",
    "3SH2": "Nbr heures hors travail",
    "m1zq": "Nbr heures temps de travail",

    "5av5": "Nom",      # page 3 - "Je soussigné(e) Mme/M" (particulier employeur)
    "5nty": "Nom AM",   # page 4 - "Je soussigné(e) Mme/M" (salarié)
}

# Dates ISO -> comb 8 cases
DATE_FIELD_MAP = {
    "1WMk": "Date de naissance",
    "h7Yb": "Date de naissancePE",
    "az5X": "Date de début",
    "31vM": "Date de fin",
    "o32r": "Date",      # page 3
    "wcq1": "Date_2",    # page 4
}

# Civilité (radio) -> valeur "/Madame" ou "/Monsieur"
CIVILITE_FIELD_MAP = {
    "bCCL": "Civilité AM",
    "tYwu": "Civilité PE",
}

# Cases à cocher simples (booléen Fillout)
CHECKBOX_FIELD_MAP = {
    "qctx": "Copie du dernier bulletin de salaire PAJEMPLOI",
    "jedN": "Relevé didentité bancaire RIB au nom et prénom de lassistant maternel",
}

# Signatures (image uploadée par Fillout) -> champ Signature du PDF
SIGNATURE_FIELD_MAP = {
    "n3hv": "Signature PE",     # page 3 - engagement du particulier employeur
    "6xK3": "SignatureAM",      # page 4 - engagement du salarié
}


def build_field_values(answers):
    field_values = []

    for key, field_id in SIMPLE_FIELD_MAP.items():
        val = answers.get(key)
        if val not in (None, ""):
            field_values.append({"field_id": field_id, "value": str(val)})

    for key, field_id in DATE_FIELD_MAP.items():
        val = answers.get(key)
        if val:
            field_values.append({"field_id": field_id, "value": ddmm_from_iso(val)})

    for key, field_id in CIVILITE_FIELD_MAP.items():
        val = answers.get(key)
        if val:
            field_values.append({"field_id": field_id, "value": f"/{val}"})

    for key, field_id in CHECKBOX_FIELD_MAP.items():
        if answers.get(key):
            field_values.append({"field_id": field_id, "value": "/On"})

    # Niveau d'étude
    niveau_label = answers.get("u2vu")
    choix = niveau_choix(niveau_label)
    if choix:
        field_values.append({"field_id": "Niveau d’étude", "value": choix})

    # Salaire horaire (question "currency" Fillout -> collection value/convertedValue)
    salaire = answers.get("bK3H") or {}
    montant = salaire.get("convertedValue") or salaire.get("value")
    if montant is not None:
        field_values.append({"field_id": "Salaire horaire", "value": f"{montant:.2f}"})

    # Cases à cocher "au choix multiple" (page 3, engagement particulier employeur)
    u5hm_items = " ".join(answers.get("u5hM") or [])
    if "versement" in u5hm_items.lower() or "spe" in u5hm_items.lower():
        field_values.append({
            "field_id": "Le versement directement à lassistant maternel de lallocation de formation si la formation se  déroule hors jour daccueil du",
            "value": "/On",
        })
    if "urssaf" in u5hm_items.lower() or "cotisation" in u5hm_items.lower():
        field_values.append({
            "field_id": "Les déclarations et le paiement des cotisations et des contributions dues auprès de lUrssaf pour le temps de formation",
            "value": "/On",
        })

    mqsf_items = " ".join(answers.get("mQSF") or [])
    if "certification et compétences" in mqsf_items.lower():
        field_values.append({"field_id": "À", "value": "/On"})
    if "ircem" in mqsf_items.lower():
        field_values.append({
            "field_id": "À lIRCEM Prévoyance le soin de procéder aux déclarations et au paiement des cotisations et des contributions dues auprès de",
            "value": "/On",
        })

    return field_values


def get_signature_urls(answers):
    """Renvoie {field_id: url_png} pour les signatures effectivement fournies."""
    sigs = {}
    for key, field_id in SIGNATURE_FIELD_MAP.items():
        arr = answers.get(key)
        if arr and isinstance(arr, list) and arr[0].get("url"):
            sigs[field_id] = arr[0]["url"]
    return sigs


# ---------------------------------------------------------------------------
# 2) Remplissage du PDF (détection auto des champs "comb")
# ---------------------------------------------------------------------------

def resolve_num(v):
    return float(v.get_object() if isinstance(v, IndirectObject) else v)


def get_widgets(writer):
    for page_index, page in enumerate(writer.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for a in annots:
            ann = a.get_object()
            if ann.get("/Subtype") != "/Widget":
                continue
            rect = ann.get("/Rect")
            if rect is not None:
                rect = [resolve_num(v) for v in rect]
                ann[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
            name = ann.get("/T")
            ft = ann.get("/FT")
            ff = ann.get("/Ff")
            maxlen = ann.get("/MaxLen")
            parent = ann.get("/Parent")
            if name is None and parent is not None:
                p = parent.get_object()
                name = p.get("/T")
                ft = ft or p.get("/FT")
                ff = ff if ff is not None else p.get("/Ff")
                maxlen = maxlen if maxlen is not None else p.get("/MaxLen")
            yield page_index, ann, name, ft, ff, maxlen, rect


def fill_pdf(template_bytes, field_values, signature_urls):
    values_by_id = {fv["field_id"]: fv["value"] for fv in field_values}

    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter(clone_from=reader)

    comb_widgets = []
    normal_values_by_page = {}
    signature_rects = {}  # field_id -> (page_index, rect)

    for page_index, ann, name, ft, ff, maxlen, rect in get_widgets(writer):
        if name in signature_urls:
            signature_rects[name] = (page_index, rect)
        if name is None or name not in values_by_id:
            continue
        value = values_by_id[name]
        is_comb = ft == "/Tx" and ff is not None and (int(ff) & COMB_BIT) and maxlen
        if is_comb:
            comb_widgets.append((page_index, rect, int(maxlen), str(value)))
        else:
            normal_values_by_page.setdefault(page_index, {})[name] = value

    for page_index, values in normal_values_by_page.items():
        writer.update_page_form_field_values(
            writer.pages[page_index], values, auto_regenerate=False
        )
    writer.set_need_appearances_writer(True)

    overlays_by_page = {}
    for page_index, rect, maxlen, value in comb_widgets:
        overlays_by_page.setdefault(page_index, []).append(("text", rect, maxlen, value))

    for field_id, (page_index, rect) in signature_rects.items():
        url = signature_urls[field_id]
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            overlays_by_page.setdefault(page_index, []).append(("image", rect, resp.content, None))
        except Exception as e:
            print(f"Signature '{field_id}' non récupérée ({e}), ignorée.")

    for page_index, entries in overlays_by_page.items():
        page = writer.pages[page_index]
        mb = page.mediabox
        w, h = float(mb.width), float(mb.height)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))

        for entry in entries:
            if entry[0] == "text":
                _, rect, maxlen, value = entry
                c.setFont("Helvetica", 10)
                c.setFillColorRGB(0.06, 0.06, 0.35)
                left, bottom, right, top = rect
                cell_w = (right - left) / maxlen
                cell_h = top - bottom
                baseline_y = bottom + cell_h * 0.30
                for i, ch in enumerate(value[:maxlen]):
                    cx = left + cell_w * i + cell_w / 2
                    c.drawCentredString(cx, baseline_y, ch)
            elif entry[0] == "image":
                _, rect, img_bytes, _ = entry
                left, bottom, right, top = rect
                box_w, box_h = right - left, top - bottom
                img = ImageReader(io.BytesIO(img_bytes))
                iw, ih = img.getSize()
                scale = min(box_w / iw, box_h / ih)
                draw_w, draw_h = iw * scale, ih * scale
                cx = left + (box_w - draw_w) / 2
                cy = bottom + (box_h - draw_h) / 2
                c.drawImage(img, cx, cy, width=draw_w, height=draw_h, mask="auto")

        c.save()
        buf.seek(0)
        overlay_reader = PdfReader(buf)
        page.merge_page(overlay_reader.pages[0])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# 3) Point d'entrée du module Make
# ---------------------------------------------------------------------------
