"""Fixtures partagées.

`REPONSES_COMPLETES` est un formulaire entièrement rempli, avec des valeurs
plausibles et *fictives* : le dépôt est public, aucune donnée réelle n'y entre.
Les clés sont celles du formulaire Fillout de production (5av5, 6qjk…).
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402

REPONSES_COMPLETES = {
    # Salarié (assistant maternel)
    "bCCL": "Madame",
    "6qjk": "Sofia",
    "rGdi": "MARTÍNEZ",
    "mDbu": "MARTÍNEZ-DUPONT",
    "1WMk": "1998-03-14",
    "pQMu": "12 rue des Lilas, bâtiment B, appartement 34",
    "nSr5": "93100",
    "kghY": "Montreuil",
    "2xYg": "sofia.martinez@example.invalid",
    "bqs8": "0612345678",
    "iaB3": "298031234567890",
    "u2vu": "Niveau 4 (Baccalauréat)",
    "5nty": "Sofia MARTÍNEZ",
    "wcq1": "2026-08-16",
    # Particulier employeur
    "tYwu": "Monsieur",
    "hRqu": "Jean-Baptiste",
    "3nU2": "DURAND",
    "4La3": "DURAND",
    "h7Yb": "1975-07-02",
    "ccFv": "48 avenue de la République",
    "wbaa": "75011",
    "cmg3": "Paris",
    "afcR": "jb.durand@example.invalid",
    "aZUq": "0698765432",
    "9zAa": "12345678901",
    "bK3H": {"value": 12.5, "convertedValue": 12.5},
    "5av5": "Jean-Baptiste DURAND",
    "o32r": "2026-08-16",
    # Formation
    "aZiu": "each One",
    "uLVH": "10 rue de Paris, 93100 Montreuil",
    "wDAp": "Français langue étrangère — niveau A2",
    "21Bn": "FLE-A2-001",
    "1s6s": "Montreuil",
    "sqyF": "120",
    "3SH2": "40",
    "m1zq": "80",
    "az5X": "2026-09-01",
    "31vM": "2026-12-15",
    # Cases et mandats
    "qctx": True,
    "jedN": True,
    "u5hM": ["Le versement directement à l'assistant maternel de l'allocation de formation",
             "Les déclarations et le paiement des cotisations auprès de l'Urssaf"],
    "mQSF": ["À la certification et compétences",
             "À l'IRCEM Prévoyance le soin de procéder aux déclarations"],
}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def reponses():
    return dict(REPONSES_COMPLETES)
