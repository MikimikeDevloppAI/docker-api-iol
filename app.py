"""
API IOL Calculator — automatisation du calculateur ESCRS (https://iolcalculator.escrs.org/)
via Selenium + Chrome headless.

Règles de confidentialité (voir README, section "Confidentialité") :
  - Aucune donnée patient n'est écrite dans les logs : ni nom du chirurgien, ni initiales,
    ni identifiant, ni âge, ni genre, ni mesures biométriques, ni lien de partage.
    Les logs ne contiennent que : l'ID technique du calcul, les *noms* des champs reçus,
    les valeurs de catalogue (fabricant, modèle d'IOL, état des switches) et les erreurs.
  - Les captures (résultat + debug) contiennent des données patient : elles sont
    supprimées automatiquement après SCREENSHOT_RETENTION_MINUTES (défaut : 60 min).
"""
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementNotInteractableException,
    StaleElementReferenceException, WebDriverException
)
import os
import sys
import re
import time
import threading
import traceback
import uuid
from datetime import datetime

# Force UTF-8 stdout/stderr (Windows cp1252 ne supporte pas les emojis)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (variables d'environnement)
# ─────────────────────────────────────────────────────────────────────────────

APP_VERSION = os.environ.get("APP_VERSION", "dev")
ESCRS_URL = os.environ.get("ESCRS_URL", "https://iolcalculator.escrs.org/")
SCREENSHOTS_DIR = os.environ.get("SCREENSHOTS_DIR", "screenshots")
DEBUG_DIR = os.path.join(SCREENSHOTS_DIR, "debug")
# Durée de conservation des captures (résultat + debug). <= 0 : conservation illimitée.
SCREENSHOT_RETENTION_MINUTES = int(os.environ.get("SCREENSHOT_RETENTION_MINUTES", "60"))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", "300"))
# Captures de debug sur erreur (contiennent des données patient, soumises à la rétention)
DEBUG_SCREENSHOTS = os.environ.get("DEBUG_SCREENSHOTS", "1") not in ("0", "false", "no")
# Délais Selenium
ELEMENT_TIMEOUT_SECONDS = int(os.environ.get("ELEMENT_TIMEOUT_SECONDS", "30"))
RESULTS_TIMEOUT_SECONDS = int(os.environ.get("RESULTS_TIMEOUT_SECONDS", "60"))
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)

# Champs valides connus (validation côté API, avant de lancer le navigateur)
VALID_TOP_FIELDS = {"surgeon", "patient_initials", "id", "age"}
TOP_FIELD_LABELS = (("surgeon", "Surgeon"), ("patient_initials", "Patient Initials"),
                    ("id", "Id"), ("age", "Age"))
VALID_EYE_FIELDS = {
    "Manufacturer", "Select IOL", "switches",
    "AL", "ACD", "LT", "CCT", "WTW", "K1", "K2", "Index", "Target Refraction",
    "Barrett A-Constant", "Cooke A-Constant", "EVO A-Constant",
    "Hill-RBF A-Constant", "Hoffer® pACD", "Kane A-Constant", "Pearl DGS A-Constant",
}
# Clé acceptée dans le payload -> début du libellé affiché sur le site ESCRS.
# Le site a renommé "Post LASIK/PRK" en "Post LASIK/PRK/RK" : on matche sur le préfixe
# pour rester compatible avec les deux écritures.
SWITCH_LABEL_PREFIX = {
    "Toric": "Toric",
    "Keratoconus": "Keratoconus",
    "Argos (SoS) AL": "Argos",
    "Post LASIK/PRK": "Post LASIK",
    "Post LASIK/PRK/RK": "Post LASIK",
}
VALID_SWITCHES = set(SWITCH_LABEL_PREFIX)
SWITCH_ORDER = ("Toric", "Keratoconus", "Argos (SoS) AL", "Post LASIK/PRK", "Post LASIK/PRK/RK")
VALID_GENDERS = {"Male", "Female"}
# Champs du payload considérés comme confidentiels : jamais loggés, jamais dans un message d'erreur.
CONFIDENTIAL_TOP_KEYS = VALID_TOP_FIELDS | {"gender"}


# ─────────────────────────────────────────────────────────────────────────────
# Logging anonymisé / Erreurs
# ─────────────────────────────────────────────────────────────────────────────

class IOLError(Exception):
    """Erreur structurée pour le calcul IOL (code machine + message + contexte)."""

    def __init__(self, code, message, context=None):
        self.code = code
        self.message = message
        self.context = context or {}
        super().__init__(f"[{code}] {message}")


_ctx = threading.local()


def set_current_calc_id(calc_id):
    _ctx.calc_id = calc_id


def _ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg):
    """Log horodaté, préfixé par l'ID technique du calcul. Ne jamais y passer de donnée patient."""
    cid = getattr(_ctx, "calc_id", None)
    prefix = f"[{_ts()}] [{cid[:8]}] " if cid else f"[{_ts()}] "
    print(prefix + str(msg), flush=True)


def describe_payload(data):
    """Résumé loggable d'un payload : uniquement les NOMS des champs et les valeurs de
    catalogue (fabricant, modèle IOL, switches). Aucune valeur patient / biométrique."""
    if not isinstance(data, dict):
        return f"<{type(data).__name__}>"
    parts = []
    parts.append("gender=" + ("set" if "gender" in data else "default"))
    top = data.get("top_fields")
    if isinstance(top, dict):
        parts.append("top_fields=" + str(sorted(top.keys())))
    for eye_key in ("right_eye", "left_eye"):
        eye = data.get(eye_key)
        if not isinstance(eye, dict):
            continue
        fields = sorted(k for k in eye if k not in ("Manufacturer", "Select IOL", "switches"))
        desc = {"fields": fields}
        if "Manufacturer" in eye:
            desc["Manufacturer"] = eye.get("Manufacturer")
        if "Select IOL" in eye:
            desc["Select IOL"] = eye.get("Select IOL")
        if isinstance(eye.get("switches"), dict):
            desc["switches"] = eye["switches"]
        parts.append(f"{eye_key}={desc}")
    return ", ".join(parts)


def save_debug_screenshot(driver, calc_id, label):
    """Capture de debug horodatée (soumise à la rétention). Désactivable via DEBUG_SCREENSHOTS=0."""
    if not driver or not calc_id or not DEBUG_SCREENSHOTS:
        return None
    try:
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:60]
        fname = f"{calc_id}_{datetime.now().strftime('%H%M%S')}_{safe_label}.png"
        path = os.path.join(DEBUG_DIR, fname)
        driver.save_screenshot(path)
        log(f"📸 Debug screenshot: {fname}")
        return path
    except Exception as e:
        log(f"⚠️ Could not save debug screenshot: {e}")
        return None


def dump_page_state(driver):
    """État de la page (URL, titre, messages d'erreur MudBlazor visibles) pour le diagnostic."""
    state = {}
    try:
        state["url"] = driver.current_url
        state["title"] = driver.title
    except Exception:
        pass
    try:
        errors = driver.find_elements(
            By.XPATH,
            "//*[contains(@class,'mud-input-error') or contains(@class,'mud-input-helper-text')"
            " or contains(@class,'mud-alert-message') or contains(@class,'validation-message')]"
        )
        msgs = sorted({e.text.strip() for e in errors if e.text.strip()})
        if msgs:
            state["page_errors"] = msgs
    except Exception:
        pass
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Rétention des captures (données patient)
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_old_screenshots(retention_minutes=None, now=None):
    """Supprime les PNG (résultats + debug) plus vieux que la rétention. Retourne le nombre supprimé."""
    retention = SCREENSHOT_RETENTION_MINUTES if retention_minutes is None else retention_minutes
    if retention is None or retention <= 0:
        return 0
    now = time.time() if now is None else now
    cutoff = now - retention * 60
    removed = 0
    for directory in (SCREENSHOTS_DIR, DEBUG_DIR):
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            continue
        for name in names:
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError as e:
                log(f"⚠️ Could not remove {name}: {e}")
    if removed:
        log(f"🧹 Removed {removed} screenshot(s) older than {retention} min")
    return removed


def start_cleanup_thread():
    """Thread démon qui purge périodiquement les captures expirées."""
    if SCREENSHOT_RETENTION_MINUTES <= 0:
        log("ℹ️ Screenshot retention disabled (SCREENSHOT_RETENTION_MINUTES <= 0)")
        return None

    def _loop():
        while True:
            try:
                cleanup_old_screenshots()
            except Exception as e:
                log(f"⚠️ Cleanup error: {e}")
            time.sleep(CLEANUP_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, name="screenshot-cleanup", daemon=True)
    t.start()
    log(f"🧹 Screenshot retention: {SCREENSHOT_RETENTION_MINUTES} min "
        f"(check every {CLEANUP_INTERVAL_SECONDS}s)")
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def web_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--headless=new')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument("--window-size=1920,1200")
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option('useAutomationExtension', False)

    if os.path.exists(CHROMEDRIVER_PATH):
        return webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=options)
    # Fallback (dev local) : selenium-manager résout le driver
    return webdriver.Chrome(options=options)


# ─────────────────────────────────────────────────────────────────────────────
# Validation du payload
# ─────────────────────────────────────────────────────────────────────────────

def validate_payload(data):
    """Valide la structure du payload. Lève IOLError si invalide.
    Les messages ne contiennent jamais de valeur patient, seulement des noms de clés."""
    if not isinstance(data, dict):
        raise IOLError("INVALID_PAYLOAD", "Payload must be a JSON object")

    if "right_eye" not in data and "left_eye" not in data:
        raise IOLError("MISSING_EYE", "At least one of 'right_eye' or 'left_eye' is required")

    gender = data.get("gender", "Female")
    if gender not in VALID_GENDERS:
        raise IOLError("INVALID_GENDER", f"Gender must be one of {sorted(VALID_GENDERS)}")

    top = data.get("top_fields", {})
    if top is None:
        top = {}
    if not isinstance(top, dict):
        raise IOLError("INVALID_TOP_FIELDS", "'top_fields' must be an object")
    unknown_top = set(top.keys()) - VALID_TOP_FIELDS
    if unknown_top:
        raise IOLError("UNKNOWN_TOP_FIELD",
                       f"Unknown top_fields: {sorted(unknown_top)}. Valid: {sorted(VALID_TOP_FIELDS)}")

    for eye_key in ("right_eye", "left_eye"):
        eye = data.get(eye_key)
        if eye is None:
            continue
        if not isinstance(eye, dict):
            raise IOLError("INVALID_EYE", f"'{eye_key}' must be an object")
        unknown = set(eye.keys()) - VALID_EYE_FIELDS
        if unknown:
            raise IOLError("UNKNOWN_EYE_FIELD",
                           f"Unknown fields in {eye_key}: {sorted(unknown)}. Valid: {sorted(VALID_EYE_FIELDS)}",
                           {"eye": eye_key, "unknown": sorted(unknown)})
        switches = eye.get("switches")
        if switches is not None:
            if not isinstance(switches, dict):
                raise IOLError("INVALID_SWITCHES", f"'{eye_key}.switches' must be an object of bool")
            unknown_sw = set(switches.keys()) - VALID_SWITCHES
            if unknown_sw:
                raise IOLError("UNKNOWN_SWITCH",
                               f"Unknown switches in {eye_key}: {sorted(unknown_sw)}. Valid: {sorted(VALID_SWITCHES)}")
            for k, v in switches.items():
                if not isinstance(v, bool):
                    raise IOLError("INVALID_SWITCH_VALUE",
                                   f"{eye_key}.switches.{k} must be true/false, got {type(v).__name__}")


# ─────────────────────────────────────────────────────────────────────────────
# Sélecteurs (centralisés pour être testables par le health-check live)
# ─────────────────────────────────────────────────────────────────────────────

XPATH_AGREE_BUTTON = "//button[.//span[normalize-space(text())='I Agree']]"
XPATH_GENDER_SELECT = "//div[contains(@class,'mud-select') and .//label[normalize-space(text())='Gender']]"
XPATH_POPOVER_OPEN = "//div[contains(@class, 'mud-popover-open')]"
XPATH_CALCULATE_BUTTON = "//button[.//span[contains(text(),'Calculate')]]"
XPATH_PRINT_BUTTON = "//button[.//span[normalize-space(text())='Print']]"
XPATH_SHARE_BUTTONS = (
    "//button[.//span[normalize-space(text())='Share']]",
    "//button[contains(text(),'Share')]",
    "//button[@title='Share']",
)
EYE_SECTIONS = (("OD Right", "OD"), ("OS Left", "OS"))


def xpath_eye_section(eye_label):
    return f"//h5[contains(text(),'{eye_label}')]/ancestor::div[contains(@class,'mud-paper')]"


def xpath_top_label(label):
    return f"//label[normalize-space(text())='{label}']"


def xpath_dropdown(dropdown_label):
    return (f".//div[contains(@class, 'mud-select') and "
            f".//label[normalize-space(text())='{dropdown_label}']]")


def xpath_dropdown_option(value):
    return f".//div[contains(@class,'mud-list-item')][.//p[normalize-space(text())='{value}']]"


def xpath_switch_input(switch_key):
    """Input checkbox d'un switch MudBlazor, identifié par le préfixe de son libellé."""
    prefix = SWITCH_LABEL_PREFIX[switch_key]
    return (f".//label[contains(@class,'mud-switch') and starts-with(normalize-space(.), '{prefix}')]"
            f"//input[@type='checkbox']")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers Selenium
# ─────────────────────────────────────────────────────────────────────────────

def get_dropdown_options(popup):
    """Libellés visibles dans un popup MudBlazor ouvert."""
    try:
        items = popup.find_elements(By.XPATH, ".//div[contains(@class,'mud-list-item')]")
        return [i.text.strip() for i in items if i.text.strip()]
    except Exception:
        return []


def select_gender(driver, wait, gender_value, calc_id):
    try:
        dropdown_container = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_GENDER_SELECT)))
        ActionChains(driver).move_to_element(dropdown_container).click().perform()
        popup = wait.until(EC.visibility_of_element_located((By.XPATH, XPATH_POPOVER_OPEN)))
        time.sleep(0.3)
        try:
            option = popup.find_element(By.XPATH, xpath_dropdown_option(gender_value))
        except NoSuchElementException:
            available = get_dropdown_options(popup)
            save_debug_screenshot(driver, calc_id, "gender_not_found")
            raise IOLError("GENDER_VALUE_NOT_FOUND", "Requested gender not in dropdown",
                           {"available": available})
        option.click()
        log("✅ Gender selected")
    except IOLError:
        raise
    except TimeoutException:
        save_debug_screenshot(driver, calc_id, "gender_dropdown_timeout")
        raise IOLError("GENDER_DROPDOWN_TIMEOUT", "Gender dropdown not found / not clickable")
    except Exception as e:
        save_debug_screenshot(driver, calc_id, "gender_unexpected_error")
        raise IOLError("GENDER_ERROR", f"Unexpected error selecting gender: {type(e).__name__}")


def select_dropdown_value(section, driver, wait, dropdown_label, value, eye_name, calc_id):
    """Sélectionne une valeur (catalogue : fabricant / modèle IOL) dans un dropdown."""
    try:
        try:
            dropdown = section.find_element(By.XPATH, xpath_dropdown(dropdown_label))
        except NoSuchElementException:
            save_debug_screenshot(driver, calc_id, f"{eye_name}_dropdown_{dropdown_label}_not_found")
            raise IOLError("DROPDOWN_NOT_FOUND",
                           f"Dropdown '{dropdown_label}' not found in {eye_name} section")

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dropdown)
        time.sleep(0.2)
        ActionChains(driver).move_to_element(dropdown).click().perform()

        try:
            popup = wait.until(EC.visibility_of_element_located((By.XPATH, XPATH_POPOVER_OPEN)))
        except TimeoutException:
            save_debug_screenshot(driver, calc_id, f"{eye_name}_popup_{dropdown_label}_timeout")
            raise IOLError("DROPDOWN_POPUP_TIMEOUT",
                           f"Popup did not open for '{dropdown_label}' in {eye_name}")

        time.sleep(0.4)
        try:
            option = popup.find_element(By.XPATH, xpath_dropdown_option(value))
        except NoSuchElementException:
            available = get_dropdown_options(popup)
            save_debug_screenshot(driver, calc_id, f"{eye_name}_{dropdown_label}_value_not_found")
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            raise IOLError("DROPDOWN_VALUE_NOT_FOUND",
                           f"Value '{value}' not found in dropdown '{dropdown_label}' ({eye_name})",
                           {"dropdown": dropdown_label, "eye": eye_name,
                            "requested": value, "available": available})

        option.click()
        time.sleep(0.7)
        log(f"✅ {eye_name}/{dropdown_label} = {value}")
    except IOLError:
        raise
    except Exception as e:
        save_debug_screenshot(driver, calc_id, f"{eye_name}_{dropdown_label}_error")
        raise IOLError("DROPDOWN_ERROR",
                       f"Unexpected error on dropdown '{dropdown_label}' ({eye_name}): {type(e).__name__}: {e}")


def set_switch(section, driver, switch_key, desired_state, eye_name, calc_id):
    """Positionne un switch (Toric, Keratoconus, Argos, Post LASIK) et vérifie l'état obtenu."""
    try:
        try:
            switch_input = section.find_element(By.XPATH, xpath_switch_input(switch_key))
        except NoSuchElementException:
            save_debug_screenshot(driver, calc_id, f"{eye_name}_switch_{switch_key}_not_found")
            raise IOLError("SWITCH_NOT_FOUND", f"Switch '{switch_key}' not found in {eye_name}")

        if switch_input.is_selected() == desired_state:
            log(f"ℹ️  {eye_name}/{switch_key} already {desired_state}")
            return

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", switch_input)
        time.sleep(0.2)
        label_el = driver.execute_script("return arguments[0].closest('label');", switch_input)
        driver.execute_script("arguments[0].click();", label_el or switch_input)
        time.sleep(0.5)

        if switch_input.is_selected() != desired_state:
            save_debug_screenshot(driver, calc_id, f"{eye_name}_switch_{switch_key}_state_mismatch")
            raise IOLError("SWITCH_STATE_MISMATCH",
                           f"Switch '{switch_key}' did not reach state {desired_state} in {eye_name}")
        log(f"✅ {eye_name}/{switch_key} set to {desired_state}")
    except IOLError:
        raise
    except Exception as e:
        save_debug_screenshot(driver, calc_id, f"{eye_name}_switch_{switch_key}_error")
        raise IOLError("SWITCH_ERROR",
                       f"Unexpected error on switch '{switch_key}' ({eye_name}): {type(e).__name__}: {e}")


def configure_switches(section, driver, switches_config, eye_name, calc_id):
    if not switches_config:
        return
    log(f"🔘 Configuring switches for {eye_name}: {switches_config}")
    done = set()
    for switch_key in SWITCH_ORDER:
        if switch_key not in switches_config:
            continue
        prefix = SWITCH_LABEL_PREFIX[switch_key]
        if prefix in done:  # alias déjà traité (Post LASIK/PRK vs /RK)
            continue
        set_switch(section, driver, switch_key, switches_config[switch_key], eye_name, calc_id)
        done.add(prefix)
    time.sleep(0.5)


def fill_top_field(driver, wait, key, label, value, calc_id):
    """Remplit un champ patient. La valeur n'est jamais loggée."""
    try:
        label_el = wait.until(EC.presence_of_element_located((By.XPATH, xpath_top_label(label))))
        input_id = label_el.get_attribute("for")
        if not input_id:
            raise IOLError("TOP_FIELD_NO_INPUT_ID", f"Label '{label}' has no 'for' attribute")
        input_el = wait.until(EC.element_to_be_clickable((By.ID, input_id)))
        input_el.clear()
        input_el.send_keys(str(value))
        log(f"✅ {label}: set")
    except TimeoutException:
        save_debug_screenshot(driver, calc_id, f"top_field_{key}_timeout")
        raise IOLError("TOP_FIELD_NOT_FOUND", f"Top field '{label}' not found within timeout")
    except IOLError:
        raise
    except Exception as e:
        save_debug_screenshot(driver, calc_id, f"top_field_{key}_error")
        raise IOLError("TOP_FIELD_ERROR", f"Error filling top field '{label}': {type(e).__name__}")


def fill_eye_inputs(section, driver, input_fields, eye_name, calc_id):
    """Remplit les champs biométriques d'un œil. Retourne la liste des champs non trouvés.
    Les valeurs ne sont jamais loggées."""
    if not input_fields:
        return []

    requested = set(input_fields.keys())
    filled = set()

    log(f"📝 Filling {len(input_fields)} fields for {eye_name}: {sorted(requested)}")
    for el in section.find_elements(By.XPATH, ".//input"):
        try:
            input_id = el.get_attribute("id")
            input_type = el.get_attribute("type")
            if input_type in ("checkbox", "radio", "hidden") or not input_id:
                continue
            label_els = section.find_elements(By.XPATH, f".//label[@for='{input_id}']")
            if not label_els:
                continue
            label = label_els[0].text.strip()
            if label not in input_fields:
                continue
            value = str(input_fields[label])
            try:
                el.click()
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.BACKSPACE)
                if label == "Target Refraction" and value.startswith("-"):
                    el.send_keys("-")
                    el.send_keys(value[1:])
                else:
                    el.send_keys(value)
                filled.add(label)
            except (ElementNotInteractableException, StaleElementReferenceException) as e:
                save_debug_screenshot(driver, calc_id, f"{eye_name}_field_{label}_not_interactable")
                raise IOLError("FIELD_NOT_INTERACTABLE",
                               f"Field '{label}' in {eye_name} is not interactable: {type(e).__name__}")
        except IOLError:
            raise
        except Exception as e:
            log(f"⚠️ Skipping a field in {eye_name} due to error: {type(e).__name__}")
            continue

    log(f"📊 Filled {len(filled)}/{len(requested)} fields for {eye_name}")
    return sorted(requested - filled)


def find_eye_section(driver, eye_label, calc_id):
    try:
        return driver.find_element(By.XPATH, xpath_eye_section(eye_label))
    except NoSuchElementException:
        save_debug_screenshot(driver, calc_id, f"section_{eye_label}_not_found")
        raise IOLError("EYE_SECTION_NOT_FOUND", f"Section '{eye_label}' not found on page")


def click_calculate(driver, wait, calc_id):
    try:
        calc_button = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_CALCULATE_BUTTON)))
    except TimeoutException:
        save_debug_screenshot(driver, calc_id, "calculate_button_not_clickable")
        raise IOLError("CALCULATE_BUTTON_NOT_CLICKABLE",
                       "Calculate button not clickable (likely missing/invalid required fields)",
                       {"page_state": dump_page_state(driver)})

    try:
        driver.execute_script("arguments[0].click();", calc_button)
        log("✅ Calculate button clicked")
    except Exception as e:
        save_debug_screenshot(driver, calc_id, "calculate_click_error")
        raise IOLError("CALCULATE_CLICK_ERROR", f"Error clicking Calculate: {type(e).__name__}")

    # Le bouton Print n'apparaît qu'avec les résultats
    try:
        WebDriverWait(driver, RESULTS_TIMEOUT_SECONDS).until(
            EC.element_to_be_clickable((By.XPATH, XPATH_PRINT_BUTTON)))
        log("✅ Results loaded")
    except TimeoutException:
        save_debug_screenshot(driver, calc_id, "results_timeout")
        raise IOLError("RESULTS_TIMEOUT",
                       f"Calculation submitted but results did not appear within {RESULTS_TIMEOUT_SECONDS}s",
                       {"page_state": dump_page_state(driver)})


def click_share_and_get_link(driver, calc_id):
    """Récupère le lien de partage ESCRS. Le lien donne accès aux données patient :
    il est renvoyé au client mais jamais loggé."""
    try:
        share_button = None
        for xpath in XPATH_SHARE_BUTTONS:
            try:
                share_button = driver.find_element(By.XPATH, xpath)
                break
            except NoSuchElementException:
                continue

        if share_button is None:
            save_debug_screenshot(driver, calc_id, "share_button_not_found")
            log("⚠️ Share button not found")
            return None

        onclick_attr = share_button.get_attribute("onclick")
        if onclick_attr:
            match = re.search(r"copyToClipboard\s*\(\s*['\"]([^'\"]+)['\"]", onclick_attr)
            if match:
                link = match.group(1)
                try:
                    driver.execute_script("arguments[0].click();", share_button)
                except Exception:
                    pass
                log("🔗 Share link extracted")
                return link

        try:
            driver.execute_script("arguments[0].click();", share_button)
            time.sleep(1.5)
            log("🔗 Share link taken from current URL")
            return driver.current_url
        except Exception:
            return None
    except Exception as e:
        log(f"⚠️ Share extraction error: {type(e).__name__}")
        return None


def take_fullpage_screenshot(driver, path):
    try:
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
        driver.set_window_size(1920, 2400)
        time.sleep(0.8)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.2)
        driver.save_screenshot(path)
        log("✅ Result screenshot saved")
        return True
    except Exception as e:
        log(f"❌ Screenshot error: {type(e).__name__}: {e}")
        return False


def open_calculator(driver, wait, calc_id):
    """Charge le site et accepte les conditions."""
    log("🔍 Navigating to ESCRS calculator...")
    try:
        driver.get(ESCRS_URL)
    except WebDriverException as e:
        raise IOLError("PAGE_LOAD_ERROR", f"Could not load IOL calculator page: {type(e).__name__}")
    try:
        wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_AGREE_BUTTON))).click()
        time.sleep(0.8)
    except TimeoutException:
        save_debug_screenshot(driver, calc_id, "agree_button_timeout")
        raise IOLError("AGREE_BUTTON_TIMEOUT", "I Agree button never appeared (page may not have loaded)")

    # Décocher la 4e checkbox (option d'affichage) si nécessaire
    try:
        fourth_checkbox = wait.until(EC.presence_of_element_located((
            By.XPATH, "(//input[@type='checkbox' and contains(@class, 'mud-checkbox-input')])[4]"
        )))
        if fourth_checkbox.get_attribute("aria-checked") != "false":
            fourth_checkbox.click()
            log("✅ 4th checkbox unchecked")
    except TimeoutException:
        log("ℹ️ 4th checkbox not present, skipping")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────────────────────────────────────

def calculate_iol(data, screenshot_path, calc_id):
    driver = None
    started = time.monotonic()
    result = {
        "success": False,
        "calculation_id": calc_id,
        "error": None,
        "screenshot_saved": False,
        "share_link": None,
        "debug_screenshots": [],
    }

    def collect_debug():
        try:
            files = [f for f in os.listdir(DEBUG_DIR) if f.startswith(calc_id + "_")]
            result["debug_screenshots"] = sorted(files)
        except Exception:
            pass

    try:
        validate_payload(data)
        log(f"📊 Payload: {describe_payload(data)}")

        top = data.get("top_fields") or {}
        right = data.get("right_eye") or {}
        left = data.get("left_eye") or {}
        gender = data.get("gender", "Female")

        log("🚀 Starting browser...")
        driver = web_driver()
        wait = WebDriverWait(driver, ELEMENT_TIMEOUT_SECONDS)

        open_calculator(driver, wait, calc_id)
        select_gender(driver, wait, gender, calc_id)

        log("📝 Filling patient information...")
        for key, label in TOP_FIELD_LABELS:
            if key in top:
                fill_top_field(driver, wait, key, label, top[key], calc_id)

        for (eye_label_h5, eye_name), eye_data in zip(EYE_SECTIONS, (right, left)):
            if not eye_data:
                continue
            log(f"👁️ Configuring {eye_name} ({eye_label_h5})...")
            section = find_eye_section(driver, eye_label_h5, calc_id)

            manufacturer = eye_data.get("Manufacturer")
            select_iol = eye_data.get("Select IOL")
            switches = eye_data.get("switches")
            input_fields = {k: v for k, v in eye_data.items()
                            if k not in ("Manufacturer", "Select IOL", "switches")}

            if switches:
                configure_switches(section, driver, switches, eye_name, calc_id)
                section = find_eye_section(driver, eye_label_h5, calc_id)  # DOM peut avoir changé

            missing = fill_eye_inputs(section, driver, input_fields, eye_name, calc_id)
            if missing:
                save_debug_screenshot(driver, calc_id, f"{eye_name}_fields_not_found")
                raise IOLError("EYE_FIELDS_NOT_FOUND",
                               f"Fields not found in {eye_name} section: {missing}",
                               {"eye": eye_name, "missing_fields": missing,
                                "hint": "Check that the field labels match exactly (case-sensitive)"})

            if manufacturer:
                select_dropdown_value(section, driver, wait, "Manufacturer", manufacturer, eye_name, calc_id)
            if select_iol:
                select_dropdown_value(section, driver, wait, "Select IOL", select_iol, eye_name, calc_id)

        save_debug_screenshot(driver, calc_id, "before_calculate")

        log("🔄 Calculating...")
        click_calculate(driver, wait, calc_id)
        time.sleep(1.5)

        result["share_link"] = click_share_and_get_link(driver, calc_id)

        log("📸 Capturing result...")
        result["screenshot_saved"] = take_fullpage_screenshot(driver, screenshot_path)
        result["success"] = True
        log(f"✅ Process completed successfully in {time.monotonic() - started:.1f}s")

    except IOLError as e:
        log(f"❌ IOLError [{e.code}]: {e.message} (after {time.monotonic() - started:.1f}s)")
        result["error"] = {"code": e.code, "message": e.message, "context": e.context}
        save_debug_screenshot(driver, calc_id, f"final_error_{e.code}")
    except Exception as e:
        log(f"❌ Unexpected error: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        result["error"] = {"code": "UNEXPECTED_ERROR", "message": f"{type(e).__name__}: {e}",
                           "context": {"traceback": traceback.format_exc()}}
        save_debug_screenshot(driver, calc_id, "unexpected_error")
    finally:
        collect_debug()
        if driver:
            log("📚 Closing browser...")
            try:
                driver.quit()
            except Exception:
                pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _error_status(result):
    code = (result.get("error") or {}).get("code", "")
    return 400 if code.startswith(("INVALID_", "UNKNOWN_", "MISSING_")) else 500


def _run_calculation():
    """Lit le body, lance le calcul. Retourne (calc_id, screenshot_path, result) ou une réponse d'erreur."""
    data = request.get_json(silent=True)
    if data is None:
        return None, None, None, (jsonify({"error": {"code": "NO_DATA",
                                                       "message": "No JSON body provided (or invalid JSON)"}}), 400)
    calc_id = str(uuid.uuid4())
    set_current_calc_id(calc_id)
    screenshot_path = os.path.join(SCREENSHOTS_DIR, f"{calc_id}.png")
    log(f"📋 New calculation {calc_id}")
    result = calculate_iol(data, screenshot_path, calc_id)
    return calc_id, screenshot_path, result, None


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "version": APP_VERSION,
                    "timestamp": datetime.now().isoformat()})


@app.route('/calculate', methods=['POST'])
def calculate():
    """Lance un calcul et renvoie le PNG du résultat. Lien de partage dans X-Share-Link."""
    try:
        calc_id, screenshot_path, result, err = _run_calculation()
        if err:
            return err

        if result["success"] and os.path.exists(screenshot_path):
            response = send_file(screenshot_path, mimetype="image/png", as_attachment=True,
                                 download_name=f"iol_calculation_{calc_id}.png")
            if result.get("share_link"):
                response.headers["X-Share-Link"] = result["share_link"]
            response.headers["X-Calculation-Id"] = calc_id
            return response

        return jsonify({
            "success": False,
            "calculation_id": calc_id,
            "error": result.get("error"),
            "debug_screenshots": result.get("debug_screenshots", []),
            "debug_url_template": f"/debug/{calc_id}/<filename>",
        }), _error_status(result)
    except Exception as e:
        log(f"❌ Server error: {type(e).__name__}: {e}")
        return jsonify({"error": {"code": "SERVER_ERROR", "message": f"{type(e).__name__}: {e}"}}), 500
    finally:
        set_current_calc_id(None)


@app.route('/calculate-json', methods=['POST'])
def calculate_json():
    """Identique à /calculate mais renvoie du JSON avec l'URL du screenshot."""
    try:
        calc_id, screenshot_path, result, err = _run_calculation()
        if err:
            return err

        payload = {
            "success": result["success"],
            "calculation_id": calc_id,
            "timestamp": datetime.now().isoformat(),
            "share_link": result.get("share_link"),
            "screenshot_url": (f"/screenshot/{calc_id}"
                               if result["success"] and os.path.exists(screenshot_path) else None),
            "debug_screenshots": result.get("debug_screenshots", []),
            "debug_url_template": f"/debug/{calc_id}/<filename>",
        }
        if not result["success"]:
            payload["error"] = result.get("error")
            return jsonify(payload), _error_status(result)
        return jsonify(payload), 200
    except Exception as e:
        log(f"❌ Server error: {type(e).__name__}: {e}")
        return jsonify({"error": {"code": "SERVER_ERROR", "message": f"{type(e).__name__}: {e}"}}), 500
    finally:
        set_current_calc_id(None)


_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_DEBUG_FILE_RE = re.compile(r"^[a-zA-Z0-9_\-]+\.png$")


@app.route('/screenshot/<calc_id>', methods=['GET'])
def get_screenshot(calc_id):
    if not _ID_RE.match(calc_id):
        return jsonify({"error": "Invalid calc_id"}), 400
    path = os.path.join(SCREENSHOTS_DIR, f"{calc_id}.png")
    if not os.path.exists(path):
        return jsonify({"error": "Screenshot not found (expired or unknown id)"}), 404
    return send_file(path, mimetype="image/png")


@app.route('/debug/<calc_id>/<filename>', methods=['GET'])
def get_debug_screenshot(calc_id, filename):
    if not _DEBUG_FILE_RE.match(filename) or not filename.startswith(calc_id + "_"):
        return jsonify({"error": "Invalid filename"}), 400
    path = os.path.join(DEBUG_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "Debug screenshot not found (expired or unknown id)"}), 404
    return send_file(path, mimetype="image/png")


@app.route('/debug/<calc_id>', methods=['GET'])
def list_debug_screenshots(calc_id):
    if not _ID_RE.match(calc_id):
        return jsonify({"error": "Invalid calc_id"}), 400
    try:
        files = sorted(f for f in os.listdir(DEBUG_DIR) if f.startswith(calc_id + "_"))
    except FileNotFoundError:
        files = []
    return jsonify({"calculation_id": calc_id, "files": files,
                    "urls": [f"/debug/{calc_id}/{f}" for f in files]})


if __name__ == '__main__':
    log(f"🚀 IOL API {APP_VERSION} starting (results timeout {RESULTS_TIMEOUT_SECONDS}s, "
        f"debug screenshots {'on' if DEBUG_SCREENSHOTS else 'off'})")
    start_cleanup_thread()
    app.run(host='0.0.0.0', port=5000, debug=False)
