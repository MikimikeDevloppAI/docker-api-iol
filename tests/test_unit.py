"""Tests unitaires : validation, anonymisation des logs, rétention, sélecteurs, endpoints.
Aucun navigateur, aucun accès réseau."""
import os
import time

import pytest

import app as iol_app
from conftest import FAKE_PATIENT, SENSITIVE_VALUES, find_sensitive, prod_like_payload


# ─────────────────────────────────────────────────────────────────────────────
# Validation du payload (erreurs 400 avant tout lancement de Chrome)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload, code", [
    ({}, "MISSING_EYE"),
    ([], "INVALID_PAYLOAD"),
    ({"gender": "Alien", "right_eye": {"AL": "23.5"}}, "INVALID_GENDER"),
    ({"right_eye": {"AL": "23.5", "UnknownField": "x"}}, "UNKNOWN_EYE_FIELD"),
    ({"right_eye": {"AL": "23.5", "switches": {"NonExistent": True}}}, "UNKNOWN_SWITCH"),
    ({"right_eye": {"AL": "23.5", "switches": {"Toric": "yes"}}}, "INVALID_SWITCH_VALUE"),
    ({"right_eye": {"AL": "23.5", "switches": ["Toric"]}}, "INVALID_SWITCHES"),
    ({"right_eye": "23.5"}, "INVALID_EYE"),
    ({"top_fields": {"nom": "x"}, "right_eye": {"AL": "23.5"}}, "UNKNOWN_TOP_FIELD"),
    ({"top_fields": "x", "right_eye": {"AL": "23.5"}}, "INVALID_TOP_FIELDS"),
])
def test_validate_payload_rejects(payload, code):
    with pytest.raises(iol_app.IOLError) as exc:
        iol_app.validate_payload(payload)
    assert exc.value.code == code


def test_validate_payload_accepts_prod_shape():
    iol_app.validate_payload(prod_like_payload())


def test_validate_payload_accepts_both_post_lasik_spellings():
    for key in ("Post LASIK/PRK", "Post LASIK/PRK/RK"):
        iol_app.validate_payload({"right_eye": {"AL": "23.5", "switches": {key: True}}})


def test_validation_error_messages_never_contain_patient_values():
    """Un genre invalide ne doit pas être recopié dans le message d'erreur."""
    with pytest.raises(iol_app.IOLError) as exc:
        iol_app.validate_payload({"gender": "SECRET_GENDER_VALUE", "right_eye": {"AL": "1"}})
    assert "SECRET_GENDER_VALUE" not in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# Anonymisation des logs
# ─────────────────────────────────────────────────────────────────────────────

def test_describe_payload_contains_field_names_but_no_values():
    summary = iol_app.describe_payload(prod_like_payload())
    # Ce qui doit être présent : noms de champs + valeurs de catalogue
    for name in ("surgeon", "patient_initials", "id", "age", "AL", "K1", "Target Refraction",
                 "HOYA", "XY1", "Toric"):
        assert name in summary
    # Ce qui ne doit JAMAIS apparaître : valeurs patient / biométriques / genre
    assert not find_sensitive(summary), "valeurs sensibles dans le résumé"


def test_describe_payload_tolerates_garbage():
    assert iol_app.describe_payload(None) == "<NoneType>"
    assert iol_app.describe_payload({"right_eye": "x", "top_fields": 3}) == "gender=default"


def test_log_prefixes_with_calc_id(capsys):
    iol_app.set_current_calc_id("abcdef12-0000-0000-0000-000000000000")
    try:
        iol_app.log("hello")
    finally:
        iol_app.set_current_calc_id(None)
    out = capsys.readouterr().out
    assert "[abcdef12] hello" in out


def test_calculate_endpoint_logs_nothing_confidential(client, capsys, monkeypatch, tmp_path):
    """Trajet complet de la requête avec le calcul mocké : aucune valeur patient dans stdout,
    y compris le lien de partage."""
    share_link = "https://iolcalculator.escrs.org/redisplay?id=SECRET_SHARE_9999"

    def fake_calculate_iol(data, screenshot_path, calc_id):
        iol_app.log(f"📊 Payload: {iol_app.describe_payload(data)}")
        with open(screenshot_path, "wb") as f:
            f.write(b"\x89PNG fake")
        return {"success": True, "calculation_id": calc_id, "error": None,
                "screenshot_saved": True, "share_link": share_link, "debug_screenshots": []}

    monkeypatch.setattr(iol_app, "calculate_iol", fake_calculate_iol)
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))

    resp = client.post("/calculate", json=prod_like_payload())
    assert resp.status_code == 200
    assert resp.headers["X-Share-Link"] == share_link  # le client, lui, reçoit bien le lien
    assert resp.headers["X-Calculation-Id"]

    out = capsys.readouterr().out
    assert "New calculation" in out
    assert not find_sensitive(out, SENSITIVE_VALUES + [share_link, "SECRET_SHARE_9999"]), "valeurs sensibles dans les logs"


def test_calculate_json_endpoint_error_path_logs_nothing_confidential(client, capsys, monkeypatch, tmp_path):
    def failing_calculate_iol(data, screenshot_path, calc_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(iol_app, "calculate_iol", failing_calculate_iol)
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    resp = client.post("/calculate-json", json=prod_like_payload())
    assert resp.status_code == 500
    assert resp.get_json()["error"]["code"] == "SERVER_ERROR"
    out = capsys.readouterr().out
    assert not find_sensitive(out, SENSITIVE_VALUES), "valeurs sensibles dans les logs"


# ─────────────────────────────────────────────────────────────────────────────
# Rétention des captures
# ─────────────────────────────────────────────────────────────────────────────

def test_cleanup_removes_only_expired_png(tmp_path, monkeypatch):
    shots = tmp_path / "screenshots"
    debug = shots / "debug"
    debug.mkdir(parents=True)
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(shots))
    monkeypatch.setattr(iol_app, "DEBUG_DIR", str(debug))

    now = time.time()
    old_result = shots / "old.png"
    old_debug = debug / "old_debug.png"
    fresh = shots / "fresh.png"
    not_png = shots / "notes.txt"
    for p in (old_result, old_debug, fresh, not_png):
        p.write_bytes(b"x")
    two_hours_ago = now - 2 * 3600
    os.utime(old_result, (two_hours_ago, two_hours_ago))
    os.utime(old_debug, (two_hours_ago, two_hours_ago))
    os.utime(not_png, (two_hours_ago, two_hours_ago))

    removed = iol_app.cleanup_old_screenshots(retention_minutes=60, now=now)

    assert removed == 2
    assert not old_result.exists()
    assert not old_debug.exists()
    assert fresh.exists()
    assert not_png.exists()


def test_cleanup_disabled_when_retention_non_positive(tmp_path, monkeypatch):
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(iol_app, "DEBUG_DIR", str(tmp_path / "debug"))
    old = tmp_path / "old.png"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))
    assert iol_app.cleanup_old_screenshots(retention_minutes=0) == 0
    assert old.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Sélecteurs
# ─────────────────────────────────────────────────────────────────────────────

def test_switch_xpath_matches_renamed_post_lasik_label():
    """Le site affiche 'Post LASIK/PRK/RK' ; la clé historique 'Post LASIK/PRK' doit y mener."""
    xp_old = iol_app.xpath_switch_input("Post LASIK/PRK")
    xp_new = iol_app.xpath_switch_input("Post LASIK/PRK/RK")
    assert xp_old == xp_new
    assert "starts-with(normalize-space(.), 'Post LASIK')" in xp_old
    assert "mud-switch" in xp_old


def test_every_valid_switch_has_an_xpath():
    for key in iol_app.VALID_SWITCHES:
        assert iol_app.xpath_switch_input(key)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints simples
# ─────────────────────────────────────────────────────────────────────────────

def test_health_reports_version(client):
    body = client.get("/health").get_json()
    assert body["status"] == "healthy"
    assert body["version"] == iol_app.APP_VERSION


def test_calculate_without_json_body_is_400(client):
    resp = client.post("/calculate", data="not json", content_type="text/plain")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "NO_DATA"


def test_validation_errors_are_400_without_launching_chrome(client, monkeypatch):
    def must_not_be_called():
        raise AssertionError("Chrome ne doit pas être lancé pour un payload invalide")

    monkeypatch.setattr(iol_app, "web_driver", must_not_be_called)
    resp = client.post("/calculate-json", json={"gender": "Alien", "right_eye": {"AL": "23.5"}})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_GENDER"


@pytest.mark.parametrize("path", [
    "/screenshot/../etc/passwd",
    "/debug/abc/../../x.png",
    "/debug/abc/other_123.png",
])
def test_file_endpoints_reject_bad_ids(client, path):
    resp = client.get(path)
    assert resp.status_code in (400, 404)


def test_unknown_screenshot_is_404(client):
    assert client.get("/screenshot/00000000-0000-0000-0000-000000000000").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Capture de l'écran d'erreur renvoyée au client
# ─────────────────────────────────────────────────────────────────────────────

def _failing_calc(code, context=None):
    def fake_calculate_iol(data, screenshot_path, calc_id):
        with open(screenshot_path, "wb") as f:
            f.write(b"PNG-ERREUR" * 100)
        return {"success": False, "calculation_id": calc_id, "screenshot_saved": False,
                "share_link": None, "debug_screenshots": [f"{calc_id}_1_x.png"],
                "error_screenshot": True,
                "error": {"code": code, "message": "Site refused: Please specify the Surgeon's name",
                          "context": context or {"page_state": {"page_errors": [
                              "Please specify the Surgeon's name", "AL must be between 15 and 40"]}}}}
    return fake_calculate_iol


def test_calculate_returns_error_screenshot_png_with_headers(client, monkeypatch, tmp_path):
    monkeypatch.setattr(iol_app, "calculate_iol", _failing_calc("CALCULATE_BUTTON_NOT_CLICKABLE"))
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    resp = client.post("/calculate", json=prod_like_payload())
    assert resp.status_code == 422
    assert resp.mimetype == "image/png"
    assert resp.data.startswith(b"PNG-ERREUR")
    assert resp.headers["X-Calculation-Status"] == "error"
    assert resp.headers["X-Error-Code"] == "CALCULATE_BUTTON_NOT_CLICKABLE"
    assert "Surgeon" in resp.headers["X-Error-Message"]
    assert "AL must be between" in resp.headers["X-Page-Errors"]
    assert resp.headers["X-Calculation-Id"]


def test_calculate_error_json_when_client_asks_for_json(client, monkeypatch, tmp_path):
    monkeypatch.setattr(iol_app, "calculate_iol", _failing_calc("RESULTS_TIMEOUT"))
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    for kwargs in ({"headers": {"Accept": "application/json"}}, {"query_string": {"format": "json"}}):
        resp = client.post("/calculate", json=prod_like_payload(), **kwargs)
        assert resp.status_code == 422
        body = resp.get_json()
        assert body["error"]["code"] == "RESULTS_TIMEOUT"
        assert body["error_screenshot_url"] == f"/screenshot/{body['calculation_id']}"
        import base64
        assert base64.b64decode(body["error_screenshot_base64"]).startswith(b"PNG-ERREUR")


def test_calculate_json_error_includes_screenshot(client, monkeypatch, tmp_path):
    monkeypatch.setattr(iol_app, "calculate_iol",
                        _failing_calc("DROPDOWN_VALUE_NOT_FOUND", {"available": ["A", "B"]}))
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    resp = client.post("/calculate-json", json=prod_like_payload())
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]["context"]["available"] == ["A", "B"]
    assert body["error_screenshot_url"]
    assert body["error_screenshot_base64"]


def test_error_without_screenshot_stays_json(client, monkeypatch, tmp_path):
    def no_browser(data, screenshot_path, calc_id):
        return {"success": False, "calculation_id": calc_id, "screenshot_saved": False,
                "share_link": None, "debug_screenshots": [], "error_screenshot": False,
                "error": {"code": "PAGE_LOAD_ERROR", "message": "x", "context": {}}}
    monkeypatch.setattr(iol_app, "calculate_iol", no_browser)
    monkeypatch.setattr(iol_app, "SCREENSHOTS_DIR", str(tmp_path))
    resp = client.post("/calculate", json=prod_like_payload())
    assert resp.status_code == 500
    assert resp.mimetype == "application/json"
    assert "error_screenshot_url" not in resp.get_json()


@pytest.mark.parametrize("code, status", [
    ("INVALID_GENDER", 400), ("UNKNOWN_SWITCH", 400), ("MISSING_EYE", 400),
    ("CALCULATE_BUTTON_NOT_CLICKABLE", 422), ("RESULTS_TIMEOUT", 422), ("NO_RESULTS", 422),
    ("DROPDOWN_VALUE_NOT_FOUND", 422), ("FIELD_VALUE_NOT_RETAINED", 422),
    ("SWITCH_NOT_FOUND", 500), ("PAGE_LOAD_ERROR", 500), ("UNEXPECTED_ERROR", 500),
])
def test_error_status_mapping(code, status):
    assert iol_app._error_status({"error": {"code": code}}) == status


def test_ascii_header_is_single_line_ascii_and_bounded():
    value = iol_app._ascii_header({"msg": "Erreur é\nligne 2", "list": list(range(5000))})
    assert "\n" not in value
    assert value.isascii()
    assert len(value) <= 4000
