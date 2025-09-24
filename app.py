from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
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
    
    # Utiliser ChromeDriver directement sans webdriver-manager
    return webdriver.Chrome(options=options)

def take_fullpage_screenshot(driver, path):
    original_size = driver.get_window_size()
    try:
        required_width = driver.execute_script('return document.body.parentNode.scrollWidth')
        required_height = driver.execute_script('return document.body.parentNode.scrollHeight')
        driver.set_window_size(required_width, required_height)
        driver.save_screenshot(path)
        print(f"✅ Screenshot saved: {path}")
        return True
    except Exception as e:
        print(f"❌ Screenshot error: {e}")
        return False
    finally:
        driver.set_window_size(original_size['width'], original_size['height'])

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

def calculate_iol(data, screenshot_path="result_screenshot.png"):
    driver = None
    result = {
        'success': False,
        'message': '',
        'screenshot_saved': False
    }
    
    try:
        top_fields = data.get("top_fields", {})
        right_eye = data.get("right_eye", {})
        left_eye = data.get("left_eye", {})
        gender = data.get("gender", "Female")
        
        print("🚀 Starting browser...")
        driver = web_driver()
        wait = WebDriverWait(driver, 60)
        
        print("📍 Navigating to site...")
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
            
            input_fields = right_eye.copy()
            if "Manufacturer" in input_fields:
                del input_fields["Manufacturer"]
            if "Select IOL" in input_fields:
                del input_fields["Select IOL"]
            
            for el in od_section.find_elements(By.XPATH, ".//input"):
                try:
                    input_id = el.get_attribute("id")
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
                            print(f"✅ {label}: {value}")
                except:
                    continue
            
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
            
            input_fields = left_eye.copy()
            if "Manufacturer" in input_fields:
                del input_fields["Manufacturer"]
            if "Select IOL" in input_fields:
                del input_fields["Select IOL"]
            
            for el in os_section.find_elements(By.XPATH, ".//input"):
                try:
                    input_id = el.get_attribute("id")
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
                            print(f"✅ {label}: {value}")
                except:
                    continue
            
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
            print("\n🔚 Closing browser...")
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
    """Endpoint principal pour lancer un calcul IOL et récupérer le screenshot"""
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
            # Retourner directement le screenshot
            return send_file(
                screenshot_path,
                mimetype='image/png',
                as_attachment=True,
                download_name=f'iol_calculation_{calc_id}.png'
            )
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