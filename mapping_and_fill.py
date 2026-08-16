"""
Traduction des réponses Fillout en valeurs de champs PDF, puis remplissage
du Bulletin d'inscription APNI.

Deux étages, volontairement séparés :

    build_field_values(answers) -> [{"field_id": …, "value": …}]
    get_signature_urls(answers) -> {field_id: url_png}
        connaissent les clés Fillout (5av5, 6qjk…) et rien du PDF.

    fill_pdf(template, valeurs, signatures) -> (pdf, signatures_manquantes)
        connaît le PDF et rien de Fillout.

C'est cette frontière qui permet à l'API de n'encoder dans l'URL de la pièce
jointe que le RÉSULTAT du mapping : le rendu se rejoue sans repasser par les
clés Fillout, et l'URL reste courte.
"""
import io
import logging
import re
import unicodedata
import zlib

import requests
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, IndirectObject, NameObject, NumberObject,
                           TextStringObject)
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

logger = logging.getLogger("apni")

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
    """Le libellé Fillout porte un suffixe (« Niveau 4 (Baccalauréat) ») : on
    compare par préfixe. Mais le préfixe ne doit pas être suivi d'un chiffre,
    sinon « Niveau 42 » cocherait « Niveau 4 » — cocher un niveau d'étude par
    accident sur un bulletin signé n'est pas rattrapable.

    L'ordre de NIVEAU_MAP fait le reste : « Niveau 3 BIS » est testé avant
    « Niveau 3 », dont il porte aussi le préfixe.
    """
    if not label:
        return None
    for prefixe, choix in NIVEAU_MAP:
        if label.startswith(prefixe):
            suite = label[len(prefixe):]
            if not suite or not suite[0].isdigit():
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


def _ascii(texte):
    """« Martínez-Dupont » -> « Martinez_Dupont ».

    Le nom part dans un nom de fichier et dans une URL : on ne laisse passer
    que de l'ASCII alphanumérique, le reste devient un souligné.
    """
    sans_accent = unicodedata.normalize("NFKD", str(texte))
    sans_accent = sans_accent.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", sans_accent)).strip("_")


def nom_fichier(answers):
    """« bulletin_apni_MARTINEZ_Sofia.pdf », d'après les réponses du salarié.

    Le nom d'usage prime sur le nom de naissance : c'est celui sous lequel la
    personne est connue. Si les deux manquent, on ne fabrique pas un nom à
    partir de rien — repli sur un nom neutre.
    """
    nom = _ascii(answers.get("mDbu") or answers.get("rGdi") or "")
    prenom = _ascii(answers.get("6qjk") or "")
    morceaux = [m for m in ("bulletin_apni", nom, prenom) if m]
    return "_".join(morceaux)[:150] + ".pdf"


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


def rogner_marges(octets):
    """Retire les marges transparentes d'une signature Fillout.

    Les PNG de signature arrivent en 1124×300 pour un trait qui en occupe le
    tiers : sans rognage, la signature est minuscule au milieu de son cadre.
    Même correctif que sur `ecf-livret-api`, où le défaut a été mesuré.

    Toute erreur ramène l'image d'origine : mal rognée vaut mieux qu'absente.
    """
    try:
        from PIL import Image  # fourni avec reportlab
    except ImportError:
        return octets
    try:
        image = Image.open(io.BytesIO(octets)).convert("RGBA")
        boite = image.getchannel("A").getbbox()
        if boite is None:  # entièrement transparente : rien à rogner
            return octets
        sortie = io.BytesIO()
        image.crop(boite).save(sortie, format="PNG")
        return sortie.getvalue()
    except Exception:
        return octets


def champ_porteur(ann):
    """Le champ qui porte la valeur : le parent s'il existe, sinon le widget.

    Sur ce template, cinq champs sont définis sur un parent dont le widget
    n'est qu'un enfant sans `/T` : « Organisme de formation », « Code du
    module », « Lieu de formation » et les deux « Civilité ».
    `update_page_form_field_values` cherche par `/T` sur l'annotation, ne les
    trouve pas, et ne les remplit pas — constaté le 2026-08-16 en relisant le
    rendu, jamais vu avant parce que le scénario n'a jamais tourné.
    """
    parent = ann.get("/Parent")
    return parent.get_object() if parent is not None else ann


def ecrire_texte(ann, valeur):
    champ_porteur(ann)[NameObject("/V")] = TextStringObject(valeur)


def cocher(ann, etat):
    """Coche un bouton : `/V` sur le champ, `/AS` sur le bon widget.

    `/V` seul ne suffit pas — c'est `/AS` qui désigne l'apparence affichée.
    Sans lui, la case reste visuellement vide alors que le formulaire la dit
    cochée : le pire des deux mondes sur un document signé.
    """
    champ = champ_porteur(ann)
    champ[NameObject("/V")] = NameObject(etat)
    for widget in (champ.get("/Kids") or [ann]):
        widget = widget.get_object()
        apparences = (widget.get("/AP") or {}).get("/N") or {}
        widget[NameObject("/AS")] = NameObject(etat if etat in apparences else "/Off")


def _flux(writer, donnees):
    flux = DecodedStreamObject()
    flux.set_data(donnees)
    return writer._add_object(flux)


def poser_calque(writer, page, calque, nom="/APNIcalque"):
    """Dépose un calque reportlab sur une page, sans toucher à son contenu.

    `page.merge_page()` ferait la même chose en une ligne, mais il recombine
    les deux flux de contenu — donc décompresse celui de la page et le
    réécrit tel quel. Sur ce template, les pages 3 et 4 pèsent 2,1 Mo une fois
    décompressées : le bulletin sortait à 5,4 Mo au lieu de 2,0 (mesuré le
    2026-08-16), au-dessus de la limite de réponse de 4,5 Mo de Vercel.

    Ici le calque devient un Form XObject — il porte ses propres ressources,
    donc aucune collision de noms à arbitrer — appelé depuis un flux ajouté à
    la fin de `/Contents`. Le flux d'origine reste compressé, intact.

    Le `q`/`Q` autour de l'ancien contenu n'est pas décoratif : si la page
    laisse la matrice de transformation modifiée, le calque se poserait de
    travers.
    """
    boite = page.mediabox
    largeur, hauteur = float(boite.width), float(boite.height)

    xobjet = DecodedStreamObject()
    xobjet.set_data(zlib.compress(calque.get_contents().get_data(), 9))
    xobjet[NameObject("/Filter")] = NameObject("/FlateDecode")
    xobjet[NameObject("/Type")] = NameObject("/XObject")
    xobjet[NameObject("/Subtype")] = NameObject("/Form")
    xobjet[NameObject("/FormType")] = NumberObject(1)
    xobjet[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(largeur), FloatObject(hauteur)])
    xobjet[NameObject("/Resources")] = calque[NameObject("/Resources")].clone(writer)
    reference = writer._add_object(xobjet)

    ressources = page[NameObject("/Resources")].get_object()
    if "/XObject" not in ressources:
        ressources[NameObject("/XObject")] = DictionaryObject()
    ressources[NameObject("/XObject")].get_object()[NameObject(nom)] = reference

    ancien = page.raw_get("/Contents")
    flux_anciens = list(ancien.get_object()) if isinstance(ancien.get_object(), ArrayObject) \
        else [ancien]
    page[NameObject("/Contents")] = ArrayObject(
        [_flux(writer, b"q\n")]
        + flux_anciens
        + [_flux(writer, b"Q\n"), _flux(writer, b"q\n%s Do\nQ\n" % nom.encode("ascii"))]
    )


def fill_pdf(template_bytes, field_values, signature_urls):
    """Renvoie (pdf_bytes, signatures_manquantes).

    Une signature injoignable n'interrompt pas la génération — décidé le
    2026-08-16 — mais elle est rendue à l'appelant, qui la signale. Elle ne
    disparaît pas en silence.
    """
    signatures_manquantes = []
    values_by_id = {fv["field_id"]: fv["value"] for fv in field_values}

    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter(clone_from=reader)

    comb_widgets = []
    signature_rects = {}  # field_id -> (page_index, rect)

    for page_index, ann, name, ft, ff, maxlen, rect in get_widgets(writer):
        if name in signature_urls:
            signature_rects[name] = (page_index, rect)
        if name is None or name not in values_by_id:
            continue
        value = values_by_id[name]
        is_comb = ft == "/Tx" and ff is not None and (int(ff) & COMB_BIT) and maxlen
        if is_comb:
            # Champ découpé en cases : le lecteur ne sait pas les centrer,
            # on dessine chaque caractère nous-mêmes.
            comb_widgets.append((page_index, rect, int(maxlen), str(value)))
        elif ft == "/Btn":
            cocher(ann, str(value))
        else:
            ecrire_texte(ann, str(value))

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
            logger.warning("signature « %s » injoignable (%s) — bulletin produit sans elle",
                           field_id, e)
            signatures_manquantes.append(field_id)

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
                img = ImageReader(io.BytesIO(rogner_marges(img_bytes)))
                iw, ih = img.getSize()
                scale = min(box_w / iw, box_h / ih)
                draw_w, draw_h = iw * scale, ih * scale
                cx = left + (box_w - draw_w) / 2
                cy = bottom + (box_h - draw_h) / 2
                c.drawImage(img, cx, cy, width=draw_w, height=draw_h, mask="auto")

        c.save()
        buf.seek(0)
        poser_calque(writer, page, PdfReader(buf).pages[0])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), signatures_manquantes
