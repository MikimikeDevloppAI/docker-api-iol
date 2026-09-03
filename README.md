# Docker API IOL Calculator

API Flask conteneurisée qui automatise le calcul d'implants intraoculaires (IOL) sur le site [ESCRS IOL Calculator](https://iolcalculator.escrs.org/) via Selenium et Chrome headless. L'API renvoie un screenshot du résultat ainsi qu'un lien partageable vers le calcul.

**Repository GitHub :** [https://github.com/MikimikeDevloppAI/docker-api-iol](https://github.com/MikimikeDevloppAI/docker-api-iol)

**Production :** [https://api-iol.vps.allia-solutions.ch](https://api-iol.vps.allia-solutions.ch) — la documentation d'infrastructure (VPS, Nginx, SSL, déploiement) est conservée en interne, hors de ce repo public.

## Fonctionnalités

- Automatisation complète du formulaire IOL (genre, infos patient, OD, OS, switches Toric / Keratoconus / Argos AL / Post LASIK-PRK-RK)
- Sélection du Manufacturer et du modèle d'IOL
- Capture d'un screenshot optimisé (1920×2400) de la page de résultats
- Récupération du lien de partage (`Share`) du calcul
- Validation du payload avant tout lancement de Chrome (erreurs 400 avec code machine)
- Erreurs structurées (`error.code`, `error.message`, `error.context`) et captures de debug sur échec
- **Logs anonymisés** et **purge automatique des captures** (voir [Confidentialité](#confidentialité))

## Stack

- Python 3.10 + Flask + flask-cors
- Selenium 4.15 + Google Chrome + ChromeDriver (installés dans l'image)
- Docker / Docker Compose
- pytest (tests unitaires + tests live contre le site ESCRS), GitHub Actions

## Démarrage rapide

```bash
git clone https://github.com/MikimikeDevloppAI/docker-api-iol.git
cd docker-api-iol
APP_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
curl http://localhost:5000/health
```

## Endpoints

### `GET /health`
Vérifie que l'API répond. Renvoie aussi `version` (valeur de `APP_VERSION` passée au build), pratique pour vérifier quelle version tourne réellement.

### `POST /calculate`
Lance un calcul et renvoie le **screenshot PNG** en pièce jointe. Le lien de partage est dans le header HTTP `X-Share-Link`, et l'ID du calcul dans `X-Calculation-Id`.

En cas d'échec **après ouverture du site** (données refusées, bouton Calculate inactif, résultats absents, modèle d'IOL inconnu...), l'API renvoie **quand même une image** : la capture pleine page de l'écran d'erreur, avec les messages de validation du site visibles. Le détail est dans les en-têtes :

| En-tête | Contenu |
|---|---|
| `X-Calculation-Status` | `error` |
| `X-Calculation-Id` | identifiant du calcul |
| `X-Error-Code` | ex. `CALCULATE_BUTTON_NOT_CLICKABLE`, `RESULTS_TIMEOUT`, `DROPDOWN_VALUE_NOT_FOUND` |
| `X-Error-Message` | message lisible |
| `X-Page-Errors` | messages affichés par le site, séparés par ` \| ` (ex. `Please specify the Surgeon's name`) |
| `X-Error-Details` | contexte complet en JSON (liste `available`, `page_state`...) |

Statut `422` quand le site a refusé les données, `500` pour une panne (site injoignable, sélecteur cassé), `400` pour un payload mal formé (réponse JSON, aucun navigateur lancé).

Pour recevoir du JSON à la place de l'image en cas d'erreur, envoyer `Accept: application/json` ou ajouter `?format=json` :
```json
{
  "success": false,
  "calculation_id": "uuid",
  "error": {"code": "DROPDOWN_VALUE_NOT_FOUND", "message": "...", "context": {"available": ["..."]}},
  "error_screenshot_url": "/screenshot/uuid",
  "error_screenshot_base64": "iVBORw0KGgo...",
  "debug_screenshots": ["uuid_HHMMSS_label.png"],
  "debug_url_template": "/debug/uuid/<filename>"
}
```

### `POST /calculate-json`
Identique à `/calculate` mais renvoie toujours du **JSON** (en cas d'erreur, avec `error_screenshot_url` et `error_screenshot_base64`) :
```json
{
  "success": true,
  "calculation_id": "uuid",
  "screenshot_url": "/screenshot/<uuid>",
  "share_link": "https://iolcalculator.escrs.org/redisplay?id=...",
  "timestamp": "...",
  "debug_screenshots": []
}
```

### `GET /screenshot/<calc_id>`
Récupère le screenshot d'un calcul (tant qu'il n'a pas été purgé, voir rétention).

### `GET /debug/<calc_id>` et `GET /debug/<calc_id>/<filename>`
Liste / récupère les captures de debug produites lors d'un calcul en échec.

## Format du payload

```json
{
  "gender": "Female",
  "top_fields": {
    "surgeon": "Dr. Smith",
    "patient_initials": "JD",
    "id": "12345",
    "age": "68"
  },
  "right_eye": {
    "Manufacturer": "Alcon",
    "Select IOL": "AcrySof SN60WF",
    "switches": {
      "Toric": false,
      "Keratoconus": false,
      "Argos (SoS) AL": false,
      "Post LASIK/PRK": false
    },
    "AL": "23.50",
    "K1": "43.50",
    "K2": "44.20",
    "ACD": "3.20",
    "Target Refraction": "-0.25"
  },
  "left_eye": {
    "Manufacturer": "Alcon",
    "Select IOL": "AcrySof SN60WF",
    "AL": "23.60",
    "K1": "43.40",
    "K2": "44.10",
    "ACD": "3.25",
    "Target Refraction": "0.00"
  }
}
```

Le switch peut être nommé `Post LASIK/PRK` (historique) ou `Post LASIK/PRK/RK` (libellé actuel du site) : les deux sont acceptés.

## Codes d'erreur

| Code | Statut | Cause | Quoi faire |
|---|---|---|---|
| `NO_DATA`, `INVALID_PAYLOAD`, `MISSING_EYE`, `INVALID_GENDER`, `UNKNOWN_TOP_FIELD`, `UNKNOWN_EYE_FIELD`, `UNKNOWN_SWITCH`, `INVALID_SWITCH_VALUE` | 400 | Payload mal formé | Corriger le payload, le message liste les clés valides |
| `DROPDOWN_VALUE_NOT_FOUND` | 422 | Fabricant ou modèle d'IOL absent de la liste ESCRS | Utiliser une valeur de `error.context.available` |
| `SWITCH_NOT_FOUND`, `EYE_FIELDS_NOT_FOUND`, `TOP_FIELD_NOT_FOUND`, `EYE_SECTION_NOT_FOUND`, `DROPDOWN_NOT_FOUND` | 500 | Le site ESCRS a changé son markup | Lancer les tests live, adapter les sélecteurs dans `app.py` |
| `CALCULATE_BUTTON_NOT_CLICKABLE`, `RESULTS_TIMEOUT` | 422 | Champs requis manquants / valeurs refusées par le site | Regarder la capture renvoyée et `X-Page-Errors` |
| `NO_RESULTS` | 422 | Le site a calculé mais affiche un tableau vide (valeurs hors plage, ex. AL 999 mm) | Regarder la capture renvoyée, corriger les mesures |
| `FIELD_VALUE_NOT_RETAINED` | 422 | Valeur refusée par le champ (lettres dans un champ numérique, format invalide) ou effacée par un re-rendu du site malgré 3 tentatives | Regarder la capture renvoyée (le champ apparaît vide), corriger la valeur |
| `AGREE_BUTTON_TIMEOUT`, `PAGE_LOAD_ERROR` | 500 | Site ESCRS injoignable ou lent | Réessayer plus tard |

## Confidentialité

Les requêtes contiennent des données patient (nom du chirurgien, initiales, identifiant, âge, genre, biométrie). Règles appliquées :

- **Logs** : aucune valeur patient n'est écrite. Les logs contiennent l'ID technique du calcul, les *noms* des champs reçus, les valeurs de catalogue (fabricant, modèle d'IOL, état des switches), les étapes et les erreurs. Le lien de partage ESCRS, qui donne accès au calcul, n'est pas loggé non plus. Des tests unitaires et live vérifient qu'aucune valeur sensible ne fuit dans la sortie standard.
- **Captures** (résultat et debug) : elles contiennent les données patient affichées par le site. Elles sont supprimées automatiquement après `SCREENSHOT_RETENTION_MINUTES` (défaut 60 min, vérification toutes les 5 min). `/screenshot/<id>` renvoie 404 une fois la capture purgée.
- **Logs Docker** : rotation configurée dans `docker-compose.yml` (3 × 10 Mo).
- **Réponses HTTP** : elles renvoient au client les informations qu'il a lui-même envoyées, elles ne sont pas stockées.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_VERSION` | `dev` | Version affichée par `/health` (argument de build) |
| `SCREENSHOT_RETENTION_MINUTES` | `60` | Durée de conservation des captures. `0` = illimité |
| `CLEANUP_INTERVAL_SECONDS` | `300` | Fréquence de la purge |
| `DEBUG_SCREENSHOTS` | `1` | `0` pour désactiver les captures de debug sur erreur |
| `ELEMENT_TIMEOUT_SECONDS` | `30` | Attente max d'un élément de la page |
| `RESULTS_TIMEOUT_SECONDS` | `60` | Attente max des résultats après clic sur Calculate |
| `ESCRS_URL` | `https://iolcalculator.escrs.org/` | URL du calculateur |
| `CHROMEDRIVER_PATH` | `/usr/local/bin/chromedriver` | Chemin du driver (fallback selenium-manager si absent) |

## Tests

```bash
pip install -r requirements.txt -r tests/requirements-dev.txt

# Unitaires : validation, anonymisation, rétention, sélecteurs, endpoints (< 1 s, sans Chrome)
pytest -m "not live"

# Live : vrai Chrome contre le site ESCRS (~4 min)
RUN_LIVE_TESTS=1 pytest -m live -v

# Live dans l'image Docker (même Chrome/ChromeDriver qu'en prod)
docker build -t docker-api-iol:test .
docker run --rm -e RUN_LIVE_TESTS=1 -v "$PWD/tests:/app/tests:ro" -v "$PWD/pytest.ini:/app/pytest.ini:ro" \
  docker-api-iol:test sh -c "pip install -q -r tests/requirements-dev.txt && pytest -m live -v"
```

Les tests live couvrent les catégories d'incidents observées en production :

0. **Valeur saisie perdue** : Blazor Server peut re-rendre un champ après la frappe et l'effacer (cause historique des « Please specify the Surgeon's name » puis 504). Chaque saisie est vérifiée et rejouée, un test le contrôle sur les 4 champs patient.
1. **Sélecteur cassé après un changement du site** (ex. switch renommé `Post LASIK/PRK` → `Post LASIK/PRK/RK`) : health-check de tous les sélecteurs, dans les deux sections œil, plus bascule réelle d'un switch.
2. **Modèle d'IOL inexistant** : l'API doit renvoyer `DROPDOWN_VALUE_NOT_FOUND` avec la liste `available`.
3. **Formulaire incomplet** : réponse rapide avec `page_state`, au lieu d'un timeout silencieux côté reverse proxy.
4. **Calcul nominal** avec la forme exacte du payload de production, et vérification qu'aucune donnée patient n'apparaît dans les logs.

GitHub Actions ([.github/workflows/tests.yml](.github/workflows/tests.yml)) exécute les tests unitaires à chaque push et PR, et les tests live dans l'image Docker après chaque push sur `master` ainsi que **chaque lundi à 06:00 UTC**. Un échec du job planifié signale un changement du site ESCRS avant qu'un utilisateur ne le subisse.

## Déploiement automatique (GitHub → VPS)

Chaque push sur `master` déclenche la chaîne **tests unitaires → tests live → déploiement**. Le job `deploy` ne tourne que si les deux jobs de tests sont verts :

1. Il crée un bundle git du commit et l'envoie au VPS en SSH (le serveur n'a pas besoin d'accéder à GitHub).
2. Sur le VPS, il met le clone à jour puis lance [deploy/update-vps.sh](deploy/update-vps.sh) : rebuild de l'image avec `APP_VERSION=<sha>`, redémarrage du service, attente que `/health` renvoie cette version. Si ce n'est pas le cas, retour automatique à la version précédente.
3. Il vérifie enfin la version par l'URL publique.

Secrets du repo : `VPS_SSH_KEY` (clé privée ed25519 dédiée, sa clé publique est dans `authorized_keys` du serveur), `VPS_KNOWN_HOSTS`, `VPS_HOST`, `VPS_USER`. Variable : `API_HEALTH_URL`.

Conséquence : **pousser sur `master` déploie en production**. Travailler sur une branche et passer par une pull request (tests unitaires) avant de fusionner. Un déploiement manuel reste possible via « Run workflow » dans l'onglet Actions, ou directement sur le serveur avec `deploy/update-vps.sh`.

## Déploiement

Un calcul dure entre 30 et 80 s. Le reverse proxy doit donc laisser au moins 180 s à l'upstream, sinon il renvoie un 504 alors que le calcul aboutit. Avec **Traefik**, il n'y a pas de timeout backend par défaut : il suffit d'exposer le service sur le port 5000 via les labels habituels (`traefik.http.services.<nom>.loadbalancer.server.port=5000`). Avec **Nginx** :

```nginx
location / {
    proxy_pass http://localhost:5000;
    proxy_read_timeout 180s;
    proxy_connect_timeout 10s;
    proxy_send_timeout 60s;
    ...
}
```

Mise à jour :
```bash
git pull
APP_VERSION=$(git rev-parse --short HEAD) docker compose up -d --build
curl -s http://localhost:5000/health   # -> "version": "<sha>"
```

Le script [deploy/install-vps.sh](deploy/install-vps.sh) automatise une installation complète sur un Ubuntu 24.04 vierge (Docker, Nginx, certbot). Il refuse de s'installer par-dessus un autre reverse proxy déjà présent sur les ports 80/443 : dans ce cas, intégrer le service `iol-api` de `docker-compose.yml` à la stack existante.

## Structure

```
.
├── app.py                  # API Flask + automatisation Selenium
├── Dockerfile              # Image Python + Chrome + ChromeDriver
├── docker-compose.yml      # Service iol-api (port 5000, healthcheck, rotation des logs)
├── requirements.txt        # Dépendances Python
├── pytest.ini
├── tests/
│   ├── conftest.py         # Fixtures, payload fictif type prod
│   ├── test_unit.py        # Sans navigateur
│   ├── test_live.py        # Contre le site ESCRS (marqueur live)
│   └── requirements-dev.txt
├── .github/workflows/tests.yml
└── screenshots/            # Volume des captures (purgées automatiquement)
```

## Notes

- Chrome tourne en mode `--headless=new` avec `--no-sandbox` (adapté à l'exécution en conteneur).
- Le formulaire utilise MudBlazor : les sélecteurs XPath ciblent les classes `mud-*` et sont centralisés en tête de `app.py` (`XPATH_*`, `xpath_*()`), ce qui permet aux tests live de les vérifier un par un.
