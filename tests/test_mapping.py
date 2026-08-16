"""Le mapping clés Fillout -> champs PDF.

Il n'a pas changé avec le correctif de 2026-08-16, mais il n'était couvert par
rien : une modification du formulaire Fillout le casserait en silence.
"""
import pytest

from mapping_and_fill import (
    build_field_values,
    ddmm_from_iso,
    get_signature_urls,
    niveau_choix,
)


def valeurs(answers):
    return {fv["field_id"]: fv["value"] for fv in build_field_values(answers)}


# --- dates ---------------------------------------------------------------

def test_date_iso_vers_comb():
    assert ddmm_from_iso("1991-12-15") == "15121991"


def test_date_avec_heure():
    assert ddmm_from_iso("1991-12-15T08:30:00Z") == "15121991"


def test_date_vide_ne_produit_rien():
    assert ddmm_from_iso("") == ""
    assert ddmm_from_iso(None) == ""


def test_les_six_dates_sont_converties(reponses):
    v = valeurs(reponses)
    assert v["Date de naissance"] == "14031998"
    assert v["Date de naissancePE"] == "02071975"
    assert v["Date de début"] == "01092026"
    assert v["Date de fin"] == "15122026"
    assert v["Date"] == "16082026"
    assert v["Date_2"] == "16082026"


# --- niveau d'étude ------------------------------------------------------

@pytest.mark.parametrize("libelle,attendu", [
    ("Non concerné", "/Choix1"),
    ("Niveau 2", "/Choix2"),
    ("Niveau 3", "/Choix4"),
    ("Niveau 4 (Baccalauréat)", "/Choix5"),
    ("Niveau 5", "/Choix6"),
    ("Niveau 6", "/Choix7"),
])
def test_niveau_detude(libelle, attendu):
    assert niveau_choix(libelle) == attendu


def test_niveau_3_bis_nest_pas_niveau_3():
    """« Niveau 3 BIS » commence par « Niveau 3 » : l'ordre de la table compte."""
    assert niveau_choix("Niveau 3 BIS") == "/Choix3"


def test_niveau_inconnu_ne_coche_rien():
    assert niveau_choix("Doctorat") is None
    assert niveau_choix("") is None
    assert niveau_choix(None) is None
    assert "Niveau d’étude" not in valeurs({"u2vu": "Doctorat"})


def test_niveau_42_ne_coche_pas_niveau_4():
    """Un préfixe suivi d'un chiffre n'est pas le même niveau."""
    assert niveau_choix("Niveau 42") is None


# --- civilité, cases à cocher, salaire ------------------------------------

def test_civilite(reponses):
    v = valeurs(reponses)
    assert v["Civilité AM"] == "/Madame"
    assert v["Civilité PE"] == "/Monsieur"


def test_cases_a_cocher(reponses):
    v = valeurs(reponses)
    assert v["Copie du dernier bulletin de salaire PAJEMPLOI"] == "/On"
    assert v["Relevé didentité bancaire RIB au nom et prénom de lassistant maternel"] == "/On"


def test_case_non_cochee_est_absente():
    assert "Copie du dernier bulletin de salaire PAJEMPLOI" not in valeurs({"qctx": False})


def test_salaire_horaire_a_deux_decimales():
    assert valeurs({"bK3H": {"convertedValue": 12.5}})["Salaire horaire"] == "12.50"


def test_mandats_multi_choix(reponses):
    v = valeurs(reponses)
    assert v["À"] == "/On"
    assert v[
        "Le versement directement à lassistant maternel de lallocation de formation si la "
        "formation se  déroule hors jour daccueil du"
    ] == "/On"


# --- champs vides ---------------------------------------------------------

def test_formulaire_vide_ne_produit_aucune_valeur():
    """Un champ absent n'écrit rien : jamais de texte de remplissage."""
    assert build_field_values({}) == []
    assert get_signature_urls({}) == {}


def test_chaine_vide_ignoree():
    assert "Prénom" not in valeurs({"6qjk": ""})


# --- signatures -----------------------------------------------------------

def test_signatures_extraites():
    sigs = get_signature_urls({
        "n3hv": [{"url": "https://exemple.invalid/pe.png"}],
        "6xK3": [{"url": "https://exemple.invalid/am.png"}],
    })
    assert sigs == {
        "Signature PE": "https://exemple.invalid/pe.png",
        "SignatureAM": "https://exemple.invalid/am.png",
    }


def test_signature_sans_url_ignoree():
    assert get_signature_urls({"n3hv": [{}]}) == {}
    assert get_signature_urls({"n3hv": []}) == {}
