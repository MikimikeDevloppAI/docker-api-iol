from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import os
import time
import traceback
from datetime import datetime
import uuid

app = Flask(__name__)
CORS(app)

# Dossier pour stocker les screenshots
SCREENSHOTS_DIR = "screenshots"
if not os.path.exists(SCREENSHOTS_DIR):
    os.makedirs(SCREENSHOTS_DIR)

def web_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument("--window-size=1920,1200")
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Utiliser le ChromeDriver installé dans le container
    service = Service('/usr/local/bin/chromedriver')
    return webdriver.Chrome(service=service, options=options)

def click_share_and_get_link(driver, wait):
    """Clique sur le bouton Share et récupère le lien copié"""
    try:
        print("\n🔗 Looking for Share button...")

        # Essayer plusieurs variantes du bouton Share
        share_button_xpaths = [
            "//button[.//span[normalize-space(text())='Share']]",
            "//button[contains(text(),'Share')]",
            "//button[@title='Share']",
            "//button[contains(@class,'share')]//span",
            "//*[contains(text(),'Share')]"
        ]

        share_button = None
        for xpath in share_button_xpaths:
            try:
                share_button = driver.find_element(By.XPATH, xpath)
                if share_button:
                    print(f"✅ Found Share button with xpath: {xpath}")
                    break
            except:
                continue

        if not share_button:
            print("⚠️ Share button not found. Available buttons:")
            try:
                buttons = driver.find_elements(By.XPATH, "//button")
                for i, btn in enumerate(buttons[:10]):  # Afficher les 10 premiers boutons
                    try:
                        btn_text = btn.text.strip()
                        if btn_text:
                            print(f"  Button {i+1}: '{btn_text}'")
                    except:
                        pass
            except:
                pass
            return None

        # Extraire le lien depuis l'attribut onclick du bouton Share
        try:
            onclick_attr = share_button.get_attribute("onclick")
            print(f"📋 Share button onclick: {onclick_attr}")

            if onclick_attr:
                # Extraire l'URL entre les quotes dans copyToClipboard('URL')
                import re
                match = re.search(r"copyToClipboard\s*\(\s*['\"]([^'\"]+)['\"]", onclick_attr)
                if match:
                    share_link = match.group(1)
                    print(f"🔗 Extracted share link from onclick: {share_link}")

                    # Cliquer quand même sur le bouton pour copier dans le clipboard
                    driver.execute_script("arguments[0].scrollIntoView(true);", share_button)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", share_button)
                    print("✅ Share button clicked (link copied to clipboard)")

                    return share_link
                else:
                    print("⚠️ Could not extract URL from onclick attribute")
            else:
                print("⚠️ Share button has no onclick attribute")

        except Exception as e:
            print(f"⚠️ Error extracting link from onclick: {e}")

        # Fallback: cliquer et essayer d'autres méthodes
        try:
            driver.execute_script("arguments[0].click();", share_button)
            time.sleep(2)
            print("✅ Share button clicked")

            current_url = driver.current_url
            print(f"📋 Current URL: {current_url}")
            return current_url

        except Exception as e:
            print(f"⚠️ Could not click share button: {e}")
            return None

    except Exception as e:
        print(f"❌ Error in Share functionality: {e}")
        return None

def take_fullpage_screenshot(driver, path):
    """Capture un screenshot optimisé pour voir les résultats principaux sans scroll"""
    original_size = driver.get_window_size()
    try:
        # Scroll tout en haut de la page
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        # Redimensionner pour une vue optimale des résultats (largeur maximale, hauteur généreuse)
        # Hauteur de 2400px est suffisante pour voir tous les résultats IOL sans scroll
        optimal_width = 1920
        optimal_height = 2400

        print(f"📏 Setting optimal viewport: {optimal_width}x{optimal_height}")

        # Redimensionner la fenêtre
        driver.set_window_size(optimal_width, optimal_height)
        time.sleep(1)  # Attendre que la page se réajuste

        # S'assurer qu'on est bien en haut
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)

        # Prendre le screenshot
        driver.save_screenshot(path)
        print(f"✅ Screenshot saved: {path} ({optimal_width}x{optimal_height})")
        return True
    except Exception as e:
        print(f"❌ Screenshot error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restaurer la taille originale
        try:
            driver.set_window_size(original_size['width'], original_size['height'])
        except:
            pass

def select_gender(driver, wait, gender_value="Female"):
    try:
        dropdown_container = wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[contains(@class, 'mud-select')]"
        )))
        ActionChains(driver).move_to_element(dropdown_container).click().perform()

        dropdown_popup = wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[contains(@class, 'mud-popover-open')]"
        )))
        time.sleep(0.5)

        gender_option = dropdown_popup.find_element(
            By.XPATH, f".//div[contains(@class,'mud-list-item')][.//p[normalize-space(text())='{gender_value}']]"
        )
        gender_option.click()
        print(f"✅ Gender selected: {gender_value}")
    except Exception as e:
        print(f"❌ Failed to select gender: {e}")
        raise

def select_dropdown_value(section, driver, wait, dropdown_label, value):
    try:
        dropdown = section.find_element(
            By.XPATH, f".//div[contains(@class, 'mud-select') and .//label[normalize-space(text())='{dropdown_label}']]"
        )
        ActionChains(driver).move_to_element(dropdown).click().perform()

        popup = wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[contains(@class, 'mud-popover-open')]"
        )))
        time.sleep(0.5)

        option = popup.find_element(
            By.XPATH, f".//div[contains(@class,'mud-list-item')][.//p[normalize-space(text())='{value}']]"
        )
        option.click()
        time.sleep(1)
        print(f"✅ {dropdown_label} = {value}")
    except Exception as e:
        print(f"❌ Error selecting {dropdown_label} = {value}: {e}")
        raise

def set_switch(section, driver, switch_label, desired_state):
    """
    Active ou désactive un switch dans une section donnée (OD ou OS)
    Les switches disponibles: Toric, Keratoconus, Argos (SoS) AL, Post LASIK/PRK
    """
    try:
        # MudBlazor: le <label> du switch contient directement le texte.
        # L'ancien markup avec <p class='mud-switch'>Label</p> imbriqué n'existe plus.
        switch_input = section.find_element(
            By.XPATH,
            f".//label[normalize-space(.)='{switch_label}']"
            f"//input[@type='checkbox' and contains(@class, 'mud-switch-input')]"
        )

        # Vérifier l'état actuel du switch
        is_checked = switch_input.is_selected()

        # Si l'état actuel est différent de l'état désiré, cliquer
        if is_checked != desired_state:
            driver.execute_script("arguments[0].scrollIntoView(true);", switch_input)
            time.sleep(0.3)
            # Cliquer sur le <label> parent : MudBlazor attend l'event dessus
            label_el = driver.execute_script("return arguments[0].closest('label');", switch_input)
            driver.execute_script("arguments[0].click();", label_el or switch_input)
            time.sleep(0.5)
            print(f"✅ Switch '{switch_label}' set to: {desired_state}")
        else:
            print(f"ℹ️  Switch '{switch_label}' already at: {desired_state}")

    except Exception as e:
        print(f"⚠️ Warning: Could not set switch '{switch_label}' to {desired_state}: {e}")
        # Ne pas raise l'erreur, continuer

def configure_switches(section, driver, switches_config, eye_name):
    """Configure tous les switches d'une section (OD ou OS)"""
    if not switches_config:
        return

    print(f"\n🔘 Configuring switches for {eye_name}...")

    # Les 4 switches disponibles
    available_switches = ["Toric", "Keratoconus", "Argos (SoS) AL", "Post LASIK/PRK"]

    for switch_name in available_switches:
        if switch_name in switches_config:
            desired_state = switches_config[switch_name]
            set_switch(section, driver, switch_name, desired_state)

    # Attendre que la page se stabilise après les switches
    time.sleep(1)

def calculate_iol(data, screenshot_path="result_screenshot.png"):
    driver = None
    result = {
        'success': False,
        'message': '',
        'screenshot_saved': False,
        'share_link': None
    }

    try:
        top_fields = data.get("top_fields", {})
        right_eye = data.get("right_eye", {})
        left_eye = data.get("left_eye", {})
        gender = data.get("gender", "Female")

        print("🚀 Starting browser...")
        driver = web_driver()
        wait = WebDriverWait(driver, 60)

        print("🔍 Navigating to site...")
        driver.get("https://iolcalculator.escrs.org/")

        # Accept conditions
        print("✅ Accepting conditions...")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='I Agree']]"))).click()
        time.sleep(1)

        # Uncheck 4th checkbox if checked
        try:
            fourth_checkbox = wait.until(EC.presence_of_element_located((
                By.XPATH, "(//input[@type='checkbox' and contains(@class, 'mud-checkbox-input')])[4]"
            )))
            is_checked = fourth_checkbox.get_attribute("aria-checked")
            if is_checked != "false":
                fourth_checkbox.click()
                print("✅ 4th checkbox unchecked")
        except Exception as e:
            print(f"⚠️ Checkbox handling: {e}")

        # Select gender
        select_gender(driver, wait, gender_value=gender)

        # Fill top fields
        print("\n📝 Filling patient information...")
        field_mapping = {
            "surgeon": "Surgeon",
            "patient_initials": "Patient Initials",
            "id": "Id",
            "age": "Age"
        }

        for key, label in field_mapping.items():
            if key in top_fields:
                try:
                    label_el = wait.until(EC.presence_of_element_located(
                        (By.XPATH, f"//label[normalize-space(text())='{label}']")
                    ))
                    input_id = label_el.get_attribute("for")
                    input_el = wait.until(EC.presence_of_element_located((By.ID, input_id)))
                    input_el.clear()
                    input_el.send_keys(str(top_fields[key]))
                    print(f"✅ {label}: {top_fields[key]}")
                except Exception as e:
                    print(f"❌ Failed to fill {label}: {e}")

        # Process RIGHT EYE
        if right_eye:
            print("\n👁️ Configuring OD (Right Eye)...")
            od_section = driver.find_element(By.XPATH, "//h5[contains(text(),'OD Right')]/ancestor::div[contains(@class,'mud-paper')]")

            manufacturer = right_eye.get("Manufacturer", None)
            select_iol = right_eye.get("Select IOL", None)
            switches = right_eye.get("switches", None)

            input_fields = right_eye.copy()
            if "Manufacturer" in input_fields:
                del input_fields["Manufacturer"]
            if "Select IOL" in input_fields:
                del input_fields["Select IOL"]
            if "switches" in input_fields:
                del input_fields["switches"]

            # Configure switches FIRST (before filling fields)
            if switches:
                configure_switches(od_section, driver, switches, "OD")

            # Fill input fields
            print(f"📝 Filling {len(input_fields)} fields for OD...")
            filled_count = 0
            for el in od_section.find_elements(By.XPATH, ".//input"):
                try:
                    input_id = el.get_attribute("id")
                    input_type = el.get_attribute("type")

                    # Skip checkboxes/radios
                    if input_type in ["checkbox", "radio"]:
                        continue

                    label_el = od_section.find_elements(By.XPATH, f".//label[@for='{input_id}']")
                    if label_el:
                        label = label_el[0].text.strip()
                        if label in input_fields:
                            value = str(input_fields[label])
                            el.click()
                            el.send_keys(Keys.CONTROL, "a")
                            el.send_keys(Keys.BACKSPACE)
                            if label == "Target Refraction" and value.startswith("-"):
                                el.send_keys("-")
                                el.send_keys(value[1:])
                            else:
                                el.send_keys(value)
                            filled_count += 1
                            print(f"✅ {label}: {value}")
                except Exception as e:
                    print(f"⚠️ Error filling field: {e}")
                    continue

            print(f"📊 Filled {filled_count}/{len(input_fields)} fields for OD")

            if manufacturer:
                select_dropdown_value(od_section, driver, wait, "Manufacturer", manufacturer)
            if select_iol:
                select_dropdown_value(od_section, driver, wait, "Select IOL", select_iol)

        # Process LEFT EYE
        if left_eye:
            print("\n👁️ Configuring OS (Left Eye)...")
            os_section = driver.find_element(By.XPATH, "//h5[contains(text(),'OS Left')]/ancestor::div[contains(@class,'mud-paper')]")

            manufacturer = left_eye.get("Manufacturer", None)
            select_iol = left_eye.get("Select IOL", None)
            switches = left_eye.get("switches", None)

            input_fields = left_eye.copy()
            if "Manufacturer" in input_fields:
                del input_fields["Manufacturer"]
            if "Select IOL" in input_fields:
                del input_fields["Select IOL"]
            if "switches" in input_fields:
                del input_fields["switches"]

            # Configure switches FIRST (before filling fields)
            if switches:
                configure_switches(os_section, driver, switches, "OS")

            # Fill input fields
            print(f"📝 Filling {len(input_fields)} fields for OS...")
            filled_count = 0
            for el in os_section.find_elements(By.XPATH, ".//input"):
                try:
                    input_id = el.get_attribute("id")
                    input_type = el.get_attribute("type")

                    # Skip checkboxes/radios
                    if input_type in ["checkbox", "radio"]:
                        continue

                    label_el = os_section.find_elements(By.XPATH, f".//label[@for='{input_id}']")
                    if label_el:
                        label = label_el[0].text.strip()
                        if label in input_fields:
                            value = str(input_fields[label])
                            el.click()
                            el.send_keys(Keys.CONTROL, "a")
                            el.send_keys(Keys.BACKSPACE)
                            if label == "Target Refraction" and value.startswith("-"):
                                el.send_keys("-")
                                el.send_keys(value[1:])
                            else:
                                el.send_keys(value)
                            filled_count += 1
                            print(f"✅ {label}: {value}")
                except Exception as e:
                    print(f"⚠️ Error filling field: {e}")
                    continue

            print(f"📊 Filled {filled_count}/{len(input_fields)} fields for OS")

            if manufacturer:
                select_dropdown_value(os_section, driver, wait, "Manufacturer", manufacturer)
            if select_iol:
                select_dropdown_value(os_section, driver, wait, "Select IOL", select_iol)

        # CALCULATE
        print("\n🔄 Calculating...")
        calc_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'Calculate')]]")))
        driver.execute_script("arguments[0].click();", calc_button)
        print("✅ Calculate button clicked")

        # Wait for results
        try:
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space(text())='Print']]")))
            print("✅ Results loaded")
        except:
            print("⚠️ Print button not found, but continuing...")

        time.sleep(2)

        # Click Share and get the link
        share_link = click_share_and_get_link(driver, wait)
        if share_link:
            result['share_link'] = share_link

        # Take final screenshot
        print("\n📸 Capturing result...")
        screenshot_saved = take_fullpage_screenshot(driver, screenshot_path)

        result['success'] = True
        result['message'] = 'Calculation completed successfully'
        result['screenshot_saved'] = screenshot_saved

        print("\n✅ Process completed successfully!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        result['success'] = False
        result['message'] = str(e)
    finally:
        if driver:
            print("\n📚 Closing browser...")
            driver.quit()

    return result

# API ENDPOINTS

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de santé pour vérifier que l'API fonctionne"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/calculate', methods=['POST'])
def calculate():
    """Endpoint principal pour lancer un calcul IOL et récupérer le screenshot avec le share_link dans les headers"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Générer un nom unique pour le screenshot
        calc_id = str(uuid.uuid4())
        screenshot_filename = f"{calc_id}.png"
        screenshot_path = os.path.join(SCREENSHOTS_DIR, screenshot_filename)

        print(f"\n{'='*60}")
        print(f"📋 New calculation request: {calc_id}")
        print(f"📊 Data: {data}")
        print(f"{'='*60}\n")

        # Exécuter le calcul
        result = calculate_iol(data, screenshot_path)

        if result['success'] and os.path.exists(screenshot_path):
            # Retourner le screenshot avec le share_link dans les headers HTTP
            response = send_file(
                screenshot_path,
                mimetype='image/png',
                as_attachment=True,
                download_name=f'iol_calculation_{calc_id}.png'
            )

            # Ajouter le share_link dans les headers de la réponse
            if result.get('share_link'):
                response.headers['X-Share-Link'] = result.get('share_link')
            response.headers['X-Calculation-Id'] = calc_id

            return response
        else:
            return jsonify({
                'error': 'Calculation failed',
                'message': result.get('message', 'Unknown error'),
                'calculation_id': calc_id
            }), 500

    except Exception as e:
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/calculate-json', methods=['POST'])
def calculate_json():
    """Endpoint alternatif qui retourne les informations en JSON avec l'URL du screenshot"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Générer un nom unique pour le screenshot
        calc_id = str(uuid.uuid4())
        screenshot_filename = f"{calc_id}.png"
        screenshot_path = os.path.join(SCREENSHOTS_DIR, screenshot_filename)

        print(f"\n{'='*60}")
        print(f"📋 New calculation request: {calc_id}")
        print(f"📊 Data: {data}")
        print(f"{'='*60}\n")

        # Exécuter le calcul
        result = calculate_iol(data, screenshot_path)

        if result['success'] and os.path.exists(screenshot_path):
            return jsonify({
                'success': True,
                'calculation_id': calc_id,
                'screenshot_url': f'/screenshot/{calc_id}',
                'share_link': result.get('share_link', None),
                'message': result.get('message', 'Calculation completed'),
                'timestamp': datetime.now().isoformat()
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': 'Calculation failed',
                'message': result.get('message', 'Unknown error'),
                'calculation_id': calc_id,
                'timestamp': datetime.now().isoformat()
            }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/screenshot/<calc_id>', methods=['GET'])
def get_screenshot(calc_id):
    """Récupérer un screenshot par son ID"""
    screenshot_path = os.path.join(SCREENSHOTS_DIR, f"{calc_id}.png")

    if not os.path.exists(screenshot_path):
        return jsonify({'error': 'Screenshot not found'}), 404

    return send_file(
        screenshot_path,
        mimetype='image/png',
        as_attachment=False
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
