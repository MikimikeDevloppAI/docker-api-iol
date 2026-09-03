"""Fixtures communes.

- Les tests unitaires n'ont besoin ni de Chrome ni d'internet.
- Les tests `live` (marqueur pytest `live`) pilotent un vrai Chrome contre le site ESCRS.
  Ils ne tournent que si RUN_LIVE_TESTS=1 (ex. : dans l'image Docker ou la CI planifiée).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Isoler les captures des tests dans un dossier dédié (avant l'import de app)
os.environ.setdefault("SCREENSHOTS_DIR", os.path.join(ROOT, "screenshots", "tests"))

import app as iol_app  # noqa: E402

RUN_LIVE = os.environ.get("RUN_LIVE_TESTS", "0") not in ("0", "", "false", "no")


def pytest_collection_modifyitems(config, items):
    skip_live = pytest.mark.skip(reason="tests live désactivés (RUN_LIVE_TESTS=1 pour les lancer)")
    for item in items:
        if "live" in item.keywords and not RUN_LIVE:
            item.add_marker(skip_live)


@pytest.fixture
def client():
    iol_app.app.config["TESTING"] = True
    with iol_app.app.test_client() as c:
        yield c


# Données patient FICTIVES, faciles à repérer dans une sortie de log.
FAKE_PATIENT = {
    "surgeon": "SURGEON_SECRET_XYZ",
    "patient_initials": "INITIALS_SECRET_QRS",
    "id": "PATIENTID_SECRET_123456",
    "age": "84",
}
FAKE_EYE = {
    "AL": "23.64", "ACD": "2.660", "LT": "5.02", "CCT": "530", "WTW": "11.87",
    "K1": "42.57", "K2": "43.23", "Target Refraction": "0.00",
}
# Valeurs qu'on ne veut JAMAIS retrouver dans les logs
SENSITIVE_VALUES = list(FAKE_PATIENT.values()) + list(FAKE_EYE.values()) + ["Female", "Male"]


def find_sensitive(text, values=None):
    """Retourne les valeurs sensibles présentes dans `text`.
    Les valeurs numériques courtes (âge "84", "23.64"...) sont cherchées avec des frontières
    pour ne pas matcher par hasard un UUID, un timestamp, un compteur technique (values=84)
    ou un autre nombre. Une vraie fuite ressemble à "Age: 84" ou "'age': '84'"."""
    import re
    found = []
    for v in (SENSITIVE_VALUES if values is None else values):
        pattern = r"(?<![0-9A-Za-z:.=])" + re.escape(v) + r"(?![0-9A-Za-z:.])"
        if re.search(pattern, text):
            found.append(v)
    return found


def prod_like_payload(manufacturer="HOYA", iol="XY1", both_eyes=True):
    """Même forme que ce qu'envoie la Supabase Edge Function en production."""
    switches = {"Toric": False, "Keratoconus": False, "Argos (SoS) AL": False, "Post LASIK/PRK": False}
    eye = {"switches": switches, "Manufacturer": manufacturer, "Select IOL": iol, **FAKE_EYE}
    payload = {"gender": "Female", "top_fields": dict(FAKE_PATIENT), "right_eye": dict(eye)}
    if both_eyes:
        payload["left_eye"] = dict(eye)
    return payload


@pytest.fixture
def prod_payload():
    return prod_like_payload()
