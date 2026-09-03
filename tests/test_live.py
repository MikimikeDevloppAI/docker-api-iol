"""Tests live contre le vrai site ESCRS (Chrome headless + internet).

Lancement : RUN_LIVE_TESTS=1 pytest -m live
Dans l'image Docker :
  docker run --rm -e RUN_LIVE_TESTS=1 -v "$PWD/tests:/app/tests" docker-api-iol \
      sh -c "pip install -q -r tests/requirements-dev.txt && pytest -m live -v"

Ces tests couvrent les catégories d'erreurs observées en production :
  1. sélecteur cassé après changement de markup du site (ex. switch renommé) -> health-check
  2. modèle d'IOL inexistant -> DROPDOWN_VALUE_NOT_FOUND avec la liste 'available'
  3. résultat qui n'apparaît pas -> RESULTS_TIMEOUT / CALCULATE_BUTTON_NOT_CLICKABLE avec page_state
  4. calcul nominal complet, forme exacte du payload de production, sans fuite dans les logs
"""
import os
import time

import pytest
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import app as iol_app
from conftest import SENSITIVE_VALUES, find_sensitive, prod_like_payload

pytestmark = pytest.mark.live

EXPECTED_TOP_LABELS = ("Surgeon", "Patient Initials", "Id", "Age", "Gender")
EXPECTED_EYE_INPUTS = ("AL", "ACD", "LT", "CCT", "WTW", "K1", "K2", "Target Refraction")
EXPECTED_EYE_DROPDOWNS = ("Manufacturer", "Select IOL")
CANONICAL_SWITCHES = ("Toric", "Keratoconus", "Argos (SoS) AL", "Post LASIK/PRK")


@pytest.fixture(scope="module")
def driver():
    d = iol_app.web_driver()
    d.set_page_load_timeout(90)
    yield d
    d.quit()


@pytest.fixture(scope="module")
def calculator(driver):
    """Page chargée, conditions acceptées."""
    wait = WebDriverWait(driver, iol_app.ELEMENT_TIMEOUT_SECONDS)
    iol_app.open_calculator(driver, wait, calc_id=None)
    # Laisser Blazor finir son premier rendu (sinon StaleElementReference sur les labels)
    wait.until(EC.presence_of_element_located((By.XPATH, iol_app.xpath_eye_section("OS Left"))))
    time.sleep(1.5)
    return driver, wait


def retry_stale(fn, attempts=4, delay=0.5):
    """Blazor re-rend le DOM par morceaux : on retente une lecture sur élément obsolète."""
    for i in range(attempts):
        try:
            return fn()
        except StaleElementReferenceException:
            if i == attempts - 1:
                raise
            time.sleep(delay)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Health-check des sélecteurs : casse dès que le site change son markup
# ─────────────────────────────────────────────────────────────────────────────

def test_top_form_selectors(calculator):
    driver, wait = calculator
    for label in EXPECTED_TOP_LABELS:
        el = driver.find_element(By.XPATH, iol_app.xpath_top_label(label))
        assert el.get_attribute("for"), f"label '{label}' sans attribut for"
    assert driver.find_element(By.XPATH, iol_app.XPATH_GENDER_SELECT)
    assert driver.find_element(By.XPATH, iol_app.XPATH_CALCULATE_BUTTON)


@pytest.mark.parametrize("eye_label", [s[0] for s in iol_app.EYE_SECTIONS])
def test_eye_section_selectors(calculator, eye_label):
    driver, wait = calculator
    def read_labels():
        section = driver.find_element(By.XPATH, iol_app.xpath_eye_section(eye_label))
        return {l.text.strip() for l in section.find_elements(By.XPATH, ".//label[@for]")}

    labels = retry_stale(read_labels)
    section = driver.find_element(By.XPATH, iol_app.xpath_eye_section(eye_label))
    missing_inputs = [l for l in EXPECTED_EYE_INPUTS if l not in labels]
    assert not missing_inputs, f"{eye_label}: champs introuvables {missing_inputs}"

    for dd in EXPECTED_EYE_DROPDOWNS:
        assert section.find_elements(By.XPATH, iol_app.xpath_dropdown(dd)), f"{eye_label}: dropdown '{dd}' introuvable"

    for key in CANONICAL_SWITCHES:
        found = section.find_elements(By.XPATH, iol_app.xpath_switch_input(key))
        assert len(found) == 1, f"{eye_label}: switch '{key}' -> {len(found)} élément(s)"


def test_switch_can_be_toggled_and_verified(calculator):
    """Le clic sur un switch change réellement son état (jamais exercé en prod jusqu'ici :
    tous les calculs envoyaient False)."""
    driver, wait = calculator
    section = driver.find_element(By.XPATH, iol_app.xpath_eye_section("OD Right"))
    try:
        iol_app.set_switch(section, driver, "Toric", True, "OD", calc_id=None)
        section = driver.find_element(By.XPATH, iol_app.xpath_eye_section("OD Right"))
        assert section.find_element(By.XPATH, iol_app.xpath_switch_input("Toric")).is_selected()
    finally:
        section = driver.find_element(By.XPATH, iol_app.xpath_eye_section("OD Right"))
        iol_app.set_switch(section, driver, "Toric", False, "OD", calc_id=None)
    section = driver.find_element(By.XPATH, iol_app.xpath_eye_section("OD Right"))
    assert not section.find_element(By.XPATH, iol_app.xpath_switch_input("Toric")).is_selected()
    # L'ancienne clé doit atteindre le switch renommé 'Post LASIK/PRK/RK'
    iol_app.set_switch(section, driver, "Post LASIK/PRK", False, "OD", calc_id=None)


def test_top_fields_retain_typed_values(calculator):
    """Blazor Server peut re-rendre l'input après la frappe et effacer la valeur (cause des
    "Please specify the Surgeon's name" -> RESULTS_TIMEOUT -> 504). La saisie doit être
    vérifiée et retenue. Valeurs fictives, jamais loggées."""
    driver, wait = calculator
    typed = {"surgeon": "SURGEON_SECRET_XYZ", "patient_initials": "IS", "id": "PATIENTID_SECRET_1", "age": "84"}
    for key, label in iol_app.TOP_FIELD_LABELS:
        iol_app.fill_top_field(driver, wait, key, label, typed[key], calc_id=None)
    time.sleep(1.0)  # laisser un éventuel re-rendu tardif arriver
    for key, label in iol_app.TOP_FIELD_LABELS:
        input_id = driver.find_element(By.XPATH, iol_app.xpath_top_label(label)).get_attribute("for")
        actual = driver.find_element(By.ID, input_id).get_attribute("value")
        assert actual == typed[key], f"champ {label} : valeur perdue après saisie"


def test_manufacturer_dropdown_lists_known_brands(calculator):
    driver, wait = calculator
    section = driver.find_element(By.XPATH, iol_app.xpath_eye_section("OD Right"))
    with pytest.raises(iol_app.IOLError) as exc:
        iol_app.select_dropdown_value(section, driver, wait, "Manufacturer",
                                      "ZZZ_NOT_A_BRAND", "OD", calc_id=None)
    assert exc.value.code == "DROPDOWN_VALUE_NOT_FOUND"
    available = exc.value.context["available"]
    assert available, "liste des fabricants vide"
    assert any("Alcon" in a for a in available), available


# ─────────────────────────────────────────────────────────────────────────────
# 2..4 : scénarios de bout en bout via l'API Flask (client de test, sans serveur)
# ─────────────────────────────────────────────────────────────────────────────

def test_nominal_calculation_prod_payload(client, capsys, prod_payload):
    """Forme exacte du payload de prod (HOYA / XY1, 4 switches à False, 2 yeux)."""
    resp = client.post("/calculate-json", json=prod_payload)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["success"] is True
    assert body["share_link"] and body["share_link"].startswith("https://iolcalculator.escrs.org/")
    path = os.path.join(iol_app.SCREENSHOTS_DIR, f"{body['calculation_id']}.png")
    assert os.path.exists(path) and os.path.getsize(path) > 50_000

    out = capsys.readouterr().out
    assert "Process completed successfully" in out
    leaked = find_sensitive(out, SENSITIVE_VALUES + [body["share_link"]])
    assert not leaked, f"valeurs sensibles dans les logs : {leaked}"


def test_unknown_iol_model_reports_available_models(client, capsys):
    payload = prod_like_payload(manufacturer="HOYA", iol="ZZZ_FAKE_MODEL_999", both_eyes=False)
    resp = client.post("/calculate-json", json=payload)
    body = resp.get_json()
    assert resp.status_code == 422, body
    err = body["error"]
    assert err["code"] == "DROPDOWN_VALUE_NOT_FOUND"
    assert body["error_screenshot_url"] and body["error_screenshot_base64"]
    assert err["context"]["dropdown"] == "Select IOL"
    assert err["context"]["requested"] == "ZZZ_FAKE_MODEL_999"
    assert err["context"]["available"], "la liste des modèles disponibles doit être renvoyée"
    assert body["debug_screenshots"], "une capture de debug doit être produite sur erreur"

    out = capsys.readouterr().out
    assert not find_sensitive(out, SENSITIVE_VALUES), "valeurs sensibles dans les logs"


def test_missing_required_fields_reports_page_state(client, monkeypatch):
    """Un formulaire incomplet ne doit pas finir en 504 silencieux : l'API répond vite,
    avec un code d'erreur et l'état de la page (messages de validation du site)."""
    monkeypatch.setattr(iol_app, "RESULTS_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(iol_app, "ELEMENT_TIMEOUT_SECONDS", 15)
    payload = {"gender": "Female", "right_eye": {"AL": "23.50"}}
    resp = client.post("/calculate-json", json=payload)
    body = resp.get_json()
    assert resp.status_code == 422, body
    code = body["error"]["code"]
    assert code in ("CALCULATE_BUTTON_NOT_CLICKABLE", "RESULTS_TIMEOUT"), body
    assert "page_state" in body["error"]["context"]
    assert body["error_screenshot_url"] and len(body["error_screenshot_base64"]) > 10_000


def test_out_of_range_values_return_no_results_png(client, monkeypatch):
    """Valeurs numériques absurdes (AL 999 mm, K1 1 D) : le site calcule mais affiche un tableau
    de résultats vide. L'API doit le signaler (NO_RESULTS, 422) et renvoyer la capture."""
    monkeypatch.setattr(iol_app, "RESULTS_TIMEOUT_SECONDS", 20)
    payload = prod_like_payload(both_eyes=False)
    payload["right_eye"].update({"AL": "999", "ACD": "99", "K1": "1", "K2": "300", "CCT": "9999"})
    resp = client.post("/calculate", json=payload)
    assert resp.status_code == 422, (resp.status_code, dict(resp.headers))
    assert resp.mimetype == "image/png"
    assert len(resp.data) > 50_000
    assert resp.headers["X-Error-Code"] == "NO_RESULTS"


def test_invalid_value_returns_error_screenshot_png(client, monkeypatch):
    """Valeur invalide (lettres dans un champ numérique) : le site vide le champ, l'API le
    détecte et renvoie la CAPTURE de l'écran (PNG) avec le code d'erreur dans les en-têtes.
    (Des valeurs numériques hors plage, ex. AL=999, sont acceptées et calculées par le site.)"""
    monkeypatch.setattr(iol_app, "RESULTS_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(iol_app, "ELEMENT_TIMEOUT_SECONDS", 15)
    payload = prod_like_payload(both_eyes=False)
    payload["right_eye"].update({"AL": "abc"})
    resp = client.post("/calculate", json=payload)
    assert resp.status_code == 422, (resp.status_code, dict(resp.headers))
    assert resp.mimetype == "image/png"
    assert len(resp.data) > 50_000
    assert resp.headers["X-Calculation-Status"] == "error"
    assert resp.headers["X-Error-Code"] in ("FIELD_VALUE_NOT_RETAINED", "CALCULATE_BUTTON_NOT_CLICKABLE", "RESULTS_TIMEOUT")
    assert "OD/AL" in resp.headers["X-Error-Message"]
    path = os.path.join(iol_app.SCREENSHOTS_DIR, f"{resp.headers['X-Calculation-Id']}.png")
    assert os.path.exists(path)
