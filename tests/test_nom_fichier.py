"""Le nom sous lequel le bulletin arrive dans Airtable."""
from mapping_and_fill import nom_fichier


def test_nom_dusage_et_prenom():
    assert nom_fichier({"mDbu": "MARTINEZ", "6qjk": "Sofia"}) == "bulletin_apni_MARTINEZ_Sofia.pdf"


def test_nom_dusage_prime_sur_nom_de_naissance():
    """C'est le nom sous lequel la personne est connue."""
    assert nom_fichier({"rGdi": "MARTINEZ", "mDbu": "DUPONT", "6qjk": "Sofia"}) \
        == "bulletin_apni_DUPONT_Sofia.pdf"


def test_repli_sur_nom_de_naissance():
    assert nom_fichier({"rGdi": "MARTINEZ", "6qjk": "Sofia"}) == "bulletin_apni_MARTINEZ_Sofia.pdf"


def test_accents_translitteres():
    assert nom_fichier({"mDbu": "MARTÍNEZ-DUPONT", "6qjk": "Chloé"}) \
        == "bulletin_apni_MARTINEZ_DUPONT_Chloe.pdf"


def test_espaces_et_apostrophes():
    assert nom_fichier({"mDbu": "D'ARC LE BON", "6qjk": "Jeanne Marie"}) \
        == "bulletin_apni_D_ARC_LE_BON_Jeanne_Marie.pdf"


def test_sans_nom_ni_prenom():
    """On ne fabrique pas un nom à partir de rien."""
    assert nom_fichier({}) == "bulletin_apni.pdf"


def test_nom_entierement_non_latin():
    """Rien d'exploitable après translittération : repli, pas de nom vide."""
    assert nom_fichier({"mDbu": "日本語", "6qjk": ""}) == "bulletin_apni.pdf"


def test_prenom_seul():
    assert nom_fichier({"6qjk": "Sofia"}) == "bulletin_apni_Sofia.pdf"


def test_nom_tres_long_est_borne():
    nom = nom_fichier({"mDbu": "A" * 300, "6qjk": "B" * 300})
    assert len(nom) <= 154 and nom.endswith(".pdf")
