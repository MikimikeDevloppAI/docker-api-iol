# Brief de vérification — API IOL Calculator (nouveau serveur)

À donner à la personne ou l'IA qui intègre l'API (Supabase Edge Function / frontend).

## 1. Contexte

- **URL de base** : `https://api.srv758474.hstgr.cloud` (nouveau VPS, HTTPS valide).
- L'ancienne URL `https://api-iol.vps.allia-solutions.ch` pointe encore sur l'ancien serveur avec l'ancienne version : **ne pas l'utiliser pour ces tests**.
- L'API pilote un vrai navigateur sur le site ESCRS. Un appel dure **20 à 80 s**. Prévoir un **timeout client ≥ 180 s**. Lancer les appels **un par un**, pas en parallèle.
- **Données fictives uniquement.** Jamais de vrai patient dans les tests. Les valeurs ci-dessous sont sûres.
- L'API ne logge aucune donnée patient. Les captures sont supprimées du serveur après 60 min.

## 2. Payload de référence (même forme que la production)

```json
{
  "gender": "Female",
  "top_fields": {"surgeon": "TEST_SURGEON", "patient_initials": "TT", "id": "TEST_0001", "age": "70"},
  "right_eye": {
    "switches": {"Toric": false, "Keratoconus": false, "Argos (SoS) AL": false, "Post LASIK/PRK": false},
    "Manufacturer": "HOYA", "Select IOL": "XY1",
    "AL": "23.64", "ACD": "2.660", "LT": "5.02", "CCT": "530", "WTW": "11.87",
    "K1": "42.57", "K2": "43.23", "Target Refraction": "0.00"
  },
  "left_eye": {
    "switches": {"Toric": false, "Keratoconus": false, "Argos (SoS) AL": false, "Post LASIK/PRK": false},
    "Manufacturer": "HOYA", "Select IOL": "XY1",
    "AL": "23.63", "ACD": "2.858", "LT": "4.88", "CCT": "529", "WTW": "11.78",
    "K1": "42.37", "K2": "43.30", "Target Refraction": "0.00"
  }
}
```

Enregistrer ce JSON dans `payload.json` pour les commandes ci-dessous.

## 3. Contrat de réponse de `POST /calculate`

| Cas | Statut | Content-Type | Corps | En-têtes utiles |
|---|---|---|---|---|
| Calcul réussi | **200** | `image/png` | capture des résultats | `X-Calculation-Id`, `X-Share-Link` |
| Le site a refusé les données (champ vidé, bouton Calculate inactif, résultats absents ou vides, modèle d'IOL inconnu) | **422** | `image/png` | capture de l'écran d'erreur | `X-Calculation-Status: error`, `X-Error-Code`, `X-Error-Message`, `X-Page-Errors`, `X-Error-Details` |
| Payload mal formé | **400** | `application/json` | `{"error": {"code": ..., "message": ...}}` | — |
| Panne (site ESCRS injoignable, sélecteur cassé) | **500** | `image/png` si une page était ouverte, sinon JSON | | `X-Error-Code` |

Avec `Accept: application/json` (ou `?format=json`), les erreurs 422/500 reviennent en JSON avec `error`, `error_screenshot_url` et `error_screenshot_base64`.

`POST /calculate-json` renvoie toujours du JSON (`success`, `calculation_id`, `share_link`, `screenshot_url`, et en erreur les mêmes champs que ci-dessus).

## 4. Tests à exécuter

Pour chaque test, noter : statut HTTP, `Content-Type`, en-têtes `X-*`, durée, taille du corps, et ouvrir l'image reçue.

### T1 — Santé
```bash
curl -s https://api.srv758474.hstgr.cloud/health
```
Attendu : `{"status":"healthy","version":"<sha>",...}`. `version` doit être un SHA git (pas `dev`).

### T2 — Calcul nominal (image)
```bash
curl -s -m 300 -D headers.txt -o result.png -H "Content-Type: application/json" \
  --data @payload.json https://api.srv758474.hstgr.cloud/calculate
cat headers.txt
```
Attendu : `200`, `Content-Type: image/png`, `X-Calculation-Id` (UUID), `X-Share-Link` commençant par `https://iolcalculator.escrs.org/redisplay?id=`. `result.png` > 100 Ko et montre les tableaux OD et OS avec les colonnes Barrett, Cooke K6, EVO, Hoffer QST, Pearl DGS. Durée 20 à 80 s.
Note : un bandeau rouge « Kane: Calculation could not be completed » peut apparaître, il vient du site ESCRS, ce n'est pas une erreur de l'API.

### T3 — Calcul nominal (JSON) et récupération de la capture
```bash
curl -s -m 300 -H "Content-Type: application/json" --data @payload.json \
  https://api.srv758474.hstgr.cloud/calculate-json
```
Attendu : `200`, `"success": true`, `"share_link"`, `"screenshot_url": "/screenshot/<id>"`. Puis `GET https://api.srv758474.hstgr.cloud/screenshot/<id>` → `200` PNG (valable 60 min, ensuite `404`).

### T4 — Valeur invalide dans un champ numérique
Même payload avec `"AL": "abc"` dans `right_eye`.
Attendu : **422**, `image/png`, `X-Calculation-Status: error`, `X-Error-Code: FIELD_VALUE_NOT_RETAINED`, `X-Error-Message` mentionnant `OD/AL`. L'image montre le formulaire avec le champ AL vide et le bouton CALCULATE grisé. Durée ≈ 15 à 25 s.

### T5 — Valeurs numériques hors plage
Même payload avec, dans `right_eye` : `"AL": "999", "ACD": "99", "K1": "1", "K2": "300", "CCT": "9999"`.
Attendu : **422**, `image/png`, `X-Error-Code: NO_RESULTS`. L'image montre une page de résultats avec un tableau vide (en-tête « SE PWR (D) » sans lignes).

### T6 — Champs patient manquants
Payload sans `top_fields` : `{"gender": "Female", "right_eye": {"AL": "23.50"}}`.
Attendu : **422**, `image/png`, `X-Error-Code` = `CALCULATE_BUTTON_NOT_CLICKABLE` ou `RESULTS_TIMEOUT`, `X-Page-Errors` contenant `Surgeon`. Durée jusqu'à ≈ 90 s (l'API attend les résultats avant de conclure).

### T7 — Modèle d'IOL inconnu
Même payload avec `"Select IOL": "ZZZ_FAKE_MODEL"` (Manufacturer `HOYA`), `left_eye` retiré.
Attendu : **422**, `image/png`, `X-Error-Code: DROPDOWN_VALUE_NOT_FOUND`, `X-Error-Details` contenant `"requested": "ZZZ_FAKE_MODEL"` et une liste `"available"` non vide (les vrais modèles HOYA).

### T8 — Payload mal formé (pas de navigateur)
```bash
curl -s -i -H "Content-Type: application/json" \
  -d '{"gender":"Alien","right_eye":{"AL":"23.5"}}' https://api.srv758474.hstgr.cloud/calculate
```
Attendu : **400** JSON, `error.code = INVALID_GENDER`, réponse en moins d'une seconde.
Variante : clé inconnue `"right_eye": {"AL": "23.5", "Foo": "1"}` → `UNKNOWN_EYE_FIELD` avec la liste des clés valides dans le message.

### T9 — Erreur demandée en JSON
Test T4 avec l'en-tête `Accept: application/json`.
Attendu : **422** JSON avec `error.code`, `error_screenshot_url` et `error_screenshot_base64` (PNG décodable, > 50 Ko).

### T10 — Nouveau libellé du switch
Même payload que T2 mais avec la clé `"Post LASIK/PRK/RK": false` à la place de `"Post LASIK/PRK": false`.
Attendu : **200**, identique à T2. Les deux écritures sont acceptées.

### T11 — Deux appels consécutifs
Lancer T2 deux fois de suite. Attendu : deux `200` avec des `X-Calculation-Id` différents. Sert à vérifier la stabilité et la durée.

## 5. Ce que l'intégration (Edge Function / frontend) doit faire

1. **Timeout ≥ 180 s** sur l'appel à l'API.
2. Sur **200** : transmettre le PNG et `X-Share-Link` (comportement inchangé).
3. Sur **422** : transmettre aussi le PNG, c'est l'écran d'erreur à montrer à l'utilisateur, et afficher `X-Error-Code` + `X-Error-Message` (et `X-Page-Errors` s'il existe). Avant, l'API renvoyait un 500 avec du JSON dans ce cas.
4. Sur **400 / 500** : lire le JSON (`error.code`, `error.message`) sauf si `Content-Type` est `image/png` (500 avec capture).
5. Ne pas logger le payload côté Edge Function (données patient).
6. Une fois validé, remplacer l'URL de l'API par `https://api.srv758474.hstgr.cloud` (ou faire pointer le DNS de l'ancien domaine sur le nouveau serveur).

## 6. Format du compte rendu attendu

Pour chaque test T1 à T11 : `statut | Content-Type | X-Error-Code (si présent) | durée | taille | OK/KO + commentaire`. Joindre les images de T2, T4 et T5.
