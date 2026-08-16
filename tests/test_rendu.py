"""Le remplissage du PDF lui-même.

Trois propriétés comptent ici, et chacune correspond à un défaut réellement
rencontré le 2026-08-16 :

  - toutes les valeurs atterrissent dans le PDF, y compris celles des champs
    définis sur un parent ;
  - le fichier reste sous la limite de réponse de 4,5 Mo de Vercel ;
  - deux rendus identiques donnent les mêmes octets.
"""
import io

import pytest
from pypdf import PdfReader

import mapping_and_fill as M
from mapping_and_fill import build_field_values, fill_pdf, get_signature_urls

# Marge de réponse d'une fonction Vercel. Au-delà, Airtable ne récupère rien.
LIMITE_VERCEL = 4_500_000


@pytest.fixture(scope="module")
def template():
    with open("template.pdf", "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def rendu(template):
    from tests.conftest import REPONSES_COMPLETES
    pdf, manquantes = fill_pdf(template,
                               build_field_values(REPONSES_COMPLETES),
                               get_signature_urls(REPONSES_COMPLETES))
    return pdf, manquantes


def valeurs_du_pdf(pdf_bytes):
    """{nom de champ: valeur} telle qu'un lecteur de PDF la verrait.

    On lit la valeur sur le champ PORTEUR — le parent quand il y en a un —
    parce que c'est là qu'un lecteur va la chercher.
    """
    lues = {}
    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        for a in (page.get("/Annots") or []):
            ann = a.get_object()
            parent = ann.get("/Parent")
            porteur = parent.get_object() if parent is not None else ann
            nom = porteur.get("/T")
            if nom is not None and porteur.get("/V") is not None:
                lues[str(nom)] = str(porteur["/V"])
    return lues


def test_quatre_pages(rendu):
    assert len(PdfReader(io.BytesIO(rendu[0])).pages) == 4


def test_sous_la_limite_vercel(rendu):
    """5,4 Mo avant le 2026-08-16, quand le calque était fusionné dans la page."""
    assert len(rendu[0]) < LIMITE_VERCEL


def test_rendu_deterministe(template):
    from tests.conftest import REPONSES_COMPLETES
    fv = build_field_values(REPONSES_COMPLETES)
    sg = get_signature_urls(REPONSES_COMPLETES)
    assert fill_pdf(template, fv, sg)[0] == fill_pdf(template, fv, sg)[0]


def test_page_dorigine_non_recompressee(rendu):
    """Le calque ne doit pas faire exploser le poids du template.

    Le template pèse 1,96 Mo ; le bulletin rempli doit rester du même ordre.
    """
    assert len(rendu[0]) < 2_500_000


# --- les champs définis sur un parent -------------------------------------

CHAMPS_A_PARENT = {
    "Organisme de formation": "each One",
    "Code du module": "FLE-A2-001",
    "Lieu de formation": "Montreuil",
}


@pytest.mark.parametrize("champ,attendu", sorted(CHAMPS_A_PARENT.items()))
def test_champ_defini_sur_un_parent_est_rempli(rendu, champ, attendu):
    """Régression : ces trois champs n'ont jamais été remplis avant 2026-08-16.

    Leur widget n'a pas de `/T` — le nom est sur le parent — et
    `update_page_form_field_values` les ignorait donc en silence.
    """
    assert valeurs_du_pdf(rendu[0])[champ] == attendu


def test_civilites_cochees(rendu):
    lues = valeurs_du_pdf(rendu[0])
    assert lues["Civilité AM"] == "/Madame"
    assert lues["Civilité PE"] == "/Monsieur"


def test_apparence_du_bouton_suit_la_valeur(rendu):
    """`/V` sans `/AS` : le formulaire se dit coché mais s'affiche vide."""
    coches = {}
    for page in PdfReader(io.BytesIO(rendu[0])).pages:
        for a in (page.get("/Annots") or []):
            ann = a.get_object()
            parent = ann.get("/Parent")
            porteur = parent.get_object() if parent is not None else ann
            if str(porteur.get("/T")) == "Civilité AM":
                coches[str(ann.get("/AS"))] = coches.get(str(ann.get("/AS")), 0) + 1
    assert coches == {"/Madame": 1, "/Off": 1}


def test_niveau_detude_un_seul_coche(rendu):
    etats = []
    for page in PdfReader(io.BytesIO(rendu[0])).pages:
        for a in (page.get("/Annots") or []):
            ann = a.get_object()
            parent = ann.get("/Parent")
            porteur = parent.get_object() if parent is not None else ann
            if str(porteur.get("/T")) == "Niveau d’étude":
                etats.append(str(ann.get("/AS")))
    assert etats.count("/Choix5") == 1
    assert set(etats) == {"/Choix5", "/Off"}


def test_champs_sans_parent_toujours_remplis(rendu):
    lues = valeurs_du_pdf(rendu[0])
    assert lues["Intitulé du module"] == "Français langue étrangère — niveau A2"
    assert lues["Adresse postale"] == "10 rue de Paris, 93100 Montreuil"


# --- champs « comb » -------------------------------------------------------

def test_comb_dessines_caractere_par_caractere(rendu):
    """Les cases individuelles sont peintes sur le calque, pas dans le champ."""
    texte = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(rendu[0])).pages)
    chiffres = "".join(c for c in texte if c.isdigit())
    assert "298031234567890" in chiffres   # n° de sécurité sociale
    assert "14031998" in chiffres          # date de naissance du salarié


def test_comb_absent_des_valeurs_de_champ(rendu):
    """Sinon le lecteur écrirait la valeur PAR-DESSUS le calque."""
    assert "Numéro de sécurité sociale" not in valeurs_du_pdf(rendu[0])


# --- signatures ------------------------------------------------------------

def png_factice():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (1124, 300), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(120, 190), (600, 120), (760, 200)],
                               fill=(10, 10, 90, 255), width=9)
    tampon = io.BytesIO()
    image.save(tampon, "PNG")
    return tampon.getvalue()


def test_signature_injoignable_nempeche_pas_le_bulletin(template, monkeypatch):
    """Décidé le 2026-08-16 : le bulletin sort quand même, mais on le dit."""
    def echoue(url, timeout=15):
        raise OSError("hôte injoignable")
    monkeypatch.setattr(M.requests, "get", echoue)

    pdf, manquantes = fill_pdf(template, [], {"SignatureAM": "https://exemple.invalid/x.png"})
    assert len(pdf) > 0
    assert manquantes == ["SignatureAM"]


def test_signature_posee_est_signalee_absente_de_la_liste(template, monkeypatch):
    donnees = png_factice()
    monkeypatch.setattr(M.requests, "get",
                        lambda url, timeout=15: type("R", (), {
                            "content": donnees, "raise_for_status": lambda self: None})())
    _, manquantes = fill_pdf(template, [], {"SignatureAM": "https://exemple.invalid/x.png"})
    assert manquantes == []


def test_rognage_des_marges_transparentes():
    """Sans lui, le trait occupe le tiers de la case « signature »."""
    rognee = M.rogner_marges(png_factice())
    from PIL import Image
    assert Image.open(io.BytesIO(rognee)).size[0] < 1124


def test_rognage_dune_image_entierement_transparente():
    from PIL import Image
    tampon = io.BytesIO()
    Image.new("RGBA", (100, 50), (0, 0, 0, 0)).save(tampon, "PNG")
    octets = tampon.getvalue()
    assert M.rogner_marges(octets) == octets  # rien à rogner, on garde l'original


def test_rognage_dun_contenu_illisible():
    assert M.rogner_marges(b"ceci n'est pas une image") == b"ceci n'est pas une image"
