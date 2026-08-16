"""Le contrat HTTP, du point de vue de Make et d'Airtable."""
import main
from main import URL_MAX, _decoder, _encoder


def poster(client, reponses, **params):
    return client.post("/generate-bulletin", json={"fillout_answers": reponses}, params=params)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- encodage de la charge -------------------------------------------------

def test_aller_retour_encodage():
    valeurs = [{"field_id": "Prénom", "value": "Sofia"},
               {"field_id": "Ville", "value": "Montreuil"}]
    signatures = {"SignatureAM": "https://exemple.invalid/x.png"}
    assert _decoder(_encoder(valeurs, signatures)) == (valeurs, signatures)


def test_encodage_stable():
    """Deux fois le même formulaire -> la même URL."""
    v = [{"field_id": "b", "value": "2"}, {"field_id": "a", "value": "1"}]
    assert _encoder(v, {}) == _encoder(list(reversed(v)), {})


def test_charge_illisible():
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as e:
        _decoder("pas-du-base64-zlib")
    assert e.value.status_code == 400


# --- la réponse que Make cartographie --------------------------------------

def test_forme_de_la_reponse_airtable(client, reponses):
    r = poster(client, reponses)
    assert r.status_code == 200
    corps = r.json()
    assert corps["filename"] == "bulletin_apni_MARTINEZ_DUPONT_Sofia.pdf"
    assert corps["signatures_manquantes"] == []
    # Airtable attend exactement [{url, filename}] — c'est `{{6.data.attachment}}`
    # que le module 5 du scénario 6643033 recopie.
    assert isinstance(corps["attachment"], list) and len(corps["attachment"]) == 1
    piece = corps["attachment"][0]
    assert set(piece) == {"url", "filename"}
    assert piece["filename"] == corps["filename"]
    assert "/bulletin.pdf?d=" in piece["url"]


def test_url_bien_en_deca_du_plafond(client, reponses):
    """Formulaire entièrement rempli : mesuré à ~1 400 caractères sur 8 000."""
    url = poster(client, reponses).json()["attachment"][0]["url"]
    assert len(url) < URL_MAX / 2


def test_url_respecte_le_proxy_de_lhebergeur(client, reponses):
    """Sans x-forwarded-host, l'URL désigne l'hôte interne de la fonction."""
    r = client.post("/generate-bulletin", json={"fillout_answers": reponses},
                    headers={"x-forwarded-host": "apni.exemple.invalid",
                             "x-forwarded-proto": "https"})
    assert r.json()["attachment"][0]["url"].startswith("https://apni.exemple.invalid/bulletin.pdf")


def test_charge_trop_grosse_refusee(client, reponses, monkeypatch):
    """Mieux vaut un 422 qu'une URL qu'Airtable n'arrivera pas à ouvrir."""
    monkeypatch.setattr(main, "URL_MAX", 10)
    r = poster(client, reponses)
    assert r.status_code == 422
    assert "maximum 10" in r.json()["detail"]


# --- l'URL rend bien le même document --------------------------------------

def test_url_rend_les_memes_octets_que_le_pdf_direct(client, reponses):
    """La propriété qui fait tenir toute l'architecture.

    Airtable ne télécharge pas ce que le POST a produit : il rejoue le GET.
    Si les deux divergeaient, on archiverait un document différent de celui
    qui a été validé.
    """
    direct = poster(client, reponses, format="pdf")
    assert direct.headers["content-type"] == "application/pdf"

    url = poster(client, reponses).json()["attachment"][0]["url"]
    par_url = client.get(url[url.index("/bulletin.pdf"):])

    assert par_url.status_code == 200
    assert par_url.headers["content-type"] == "application/pdf"
    assert par_url.content == direct.content


def test_pdf_direct_porte_le_nom_de_fichier(client, reponses):
    entete = poster(client, reponses, format="pdf").headers["content-disposition"]
    assert 'filename="bulletin_apni_MARTINEZ_DUPONT_Sofia.pdf"' in entete


def test_bulletin_sans_charge_rend_le_template_vierge(client):
    r = client.get("/bulletin.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_charge_corrompue_refusee(client):
    assert client.get("/bulletin.pdf", params={"d": "n'importe quoi"}).status_code == 400


# --- corps mal formé -------------------------------------------------------

def test_corps_sans_fillout_answers(client):
    assert client.post("/generate-bulletin", json={"autre": {}}).status_code == 400


def test_corps_non_json(client):
    r = client.post("/generate-bulletin", content=b"pas du json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_formulaire_vide_reste_acceptable(client):
    """Une soumission incomplète produit un bulletin incomplet, pas une erreur."""
    r = poster(client, {})
    assert r.status_code == 200
    assert r.json()["filename"] == "bulletin_apni.pdf"


# --- signalement des signatures --------------------------------------------

def test_entete_signale_les_signatures_manquantes(client, reponses, monkeypatch):
    def echoue(url, timeout=15):
        raise OSError("hôte injoignable")
    monkeypatch.setattr(main, "fill_pdf", main.fill_pdf)
    import mapping_and_fill
    monkeypatch.setattr(mapping_and_fill.requests, "get", echoue)

    reponses["6xK3"] = [{"url": "https://exemple.invalid/am.png"}]
    r = poster(client, reponses)
    assert r.status_code == 200
    assert r.headers["x-apni-signatures-manquantes"] == "SignatureAM"
    assert r.json()["signatures_manquantes"] == ["SignatureAM"]
