# Reconnect Chrome Profile — Reconnexion automatique + résolution CAPTCHA

Commande dédiée à la reconnexion d'un profil Chrome GCS via `chrome_profile_server.py`.
Utilisée par `monitor-scrapper` (étape 3b-Chrome) et par tout agent ayant besoin
de rétablir une session.

> **Isolation de contexte :** quand `monitor-scrapper` reconnecte plusieurs profils, il ne lance pas
> cette commande directement — il spawne **un sous-agent par profil** (ÉTAPE 3b-Chrome Étape 4 de
> `monitor-scrapper.md`). Cette commande s'exécute donc **déjà dans un contexte isolé et jetable**,
> propre à un seul profil.
>
> ⛔ **Dans ce contexte, lis les screenshots et les images CAPTCHA TOI-MÊME, directement.** Ne spawne
> **aucun** sous-agent supplémentaire pour lire des images. Raison : le runtime de l'agent n'a pas
> d'outil de sous-agent fiable — il part en `ToolSearch` en boucle, finit par pip-installer le SDK
> Anthropic et improviser via Bash (~1 min perdue **par image**), ce qui fait **expirer la session
> reCAPTCHA (~2-3 min)** avant la résolution. L'isolation **par profil** suffit déjà à éviter
> l'accumulation d'images dans la session parente `monitor-scrapper` ; à l'intérieur d'un profil,
> quelques rounds lus directement ne saturent rien et c'est **beaucoup plus rapide** (seul moyen de
> battre le TTL du reCAPTCHA).

**Script principal :** `/workspace/scripts/monitoring/chrome_profile_server.py`

> ⚠️ **Script partagé entre Facebook, LinkedIn et Truth Social** (`_PLATFORM_CONFIG`).
> Toute correction de fonction partagée (`_detect_state`, `_CAPTCHA_DETECT_JS`, `_2FA_MARKERS_JS`…)
> doit être **générique** (comportement universel Playwright, pas DOM spécifique à une plateforme).
> Particularités propres à une plateforme → `_PLATFORM_CONFIG`. Si tu modifies du code partagé,
> le mentionner explicitement dans le rapport pour qu'un test croisé soit fait avant le prochain déploiement.

> 🚫 **Tu ne peux pas pousser ces modifications toi-même.** `/workspace/scripts/monitoring/` est
> copié dans l'image au build — il n'est pas dans ton sparse-checkout git. Tes éditions sont
> éphémères (perdues au recyclage de l'instance). **Recopie le contenu intégral des fichiers
> modifiés dans ton rapport final** — un humain les appliquera dans
> `functions/admin/gcp-fct-monitoring-agent/scripts/`.

---

## Arguments

| Argument | Description | Exemple |
|----------|-------------|---------|
| `platform` | Nom de la plateforme | `Facebook`, `LinkedIn`, `Truth Social` |
| `apikey_id` | ID de l'ApiKey en DB à reconnecter | `24`, `25` |

**Si `apikey_id` n'est pas fourni**, le découvrir depuis la DB :

```bash
PLATFORM=<Facebook|LinkedIn|Truth Social>
python3 -c "
from gcpOpsci.db import get_db
from gcpOpsci.models import ApiKey
db = next(get_db())
keys = db.query(ApiKey).filter_by(platform='$PLATFORM', status='active').order_by(ApiKey.last_used_at.desc()).all()
for k in keys:
    d = k.json_data or {}
    print(f'id={k.id} | username={d.get(\"username\")} | profile={d.get(\"chrome_profile_path\")} | last_used={k.last_used_at}')
db.close()
"
APIKEY_ID=<id choisi>
```

---

## Workflow complet

> **Cycle de vie du statut de l'ApiKey — ⛔ ne jamais réactiver avant de reconnecter.**
> Le serveur charge la clé **quel que soit son statut** (une clé en cours de reconnexion est
> souvent déjà `disactive`, isolée par le triage). Donc :
> - **Avant / pendant** la reconnexion : laisser le statut tel quel (souvent `disactive`).
> - **Après `logged_in=true`** seulement : passer la clé à `active` (et, en contexte triage,
>   remonter `max_concurrent_dispatches` de +1).
> - **Échec** (2FA, CAPTCHA insoluble…) : la clé **reste `disactive`** — rien à re-désactiver.
>
> Ne réactive donc jamais la clé « pour pouvoir lancer le serveur » : c'est inutile et ça laisse
> un compte mort `active` si la reconnexion échoue.

### 1 — Démarrer le serveur

```bash
SCREENSHOT_DIR=/tmp/cpm_${APIKEY_ID}_$(date +%s)
mkdir -p $SCREENSHOT_DIR
PORT=8231

python /workspace/scripts/monitoring/chrome_profile_server.py \
  --apikey-id $APIKEY_ID \
  --port $PORT \
  --screenshot-dir $SCREENSHOT_DIR &

## Attendre que Chrome soit prêt (~5-10s)
for i in $(seq 1 30); do
  curl -sf http://localhost:$PORT/health && echo " ✅ prêt" && break
  sleep 1
done
```

Le serveur charge les credentials depuis la DB, monte le profil GCS, démarre Xvfb + Chrome.

### 2 — Boucler sur POST /login

Appeler `/login` en boucle. Chaque appel exécute **une étape** de la machine à états et retourne un `status`.

> ⛔ **Capture l'état RÉEL de la page AVANT de remplir — ne suppose jamais la forme du formulaire.**
> `/login` **remplit ET soumet** dès qu'il détecte l'état `login_form` (dans le même appel). Et le
> serveur ne reconnaît une bannière que via des libellés connus (`cookie_consent_keywords`) — une
> **bannière cookies / consent non reconnue** n'est pas gérée : le serveur soumet quand même, la bannière
> bloque la soumission, et ça **ressemble à un `login_rejected`** alors que les credentials sont bons.
>
> Donc **avant la boucle `/login`**, appelle `/state` (qui détecte **sans agir**), **lis le screenshot
> toi-même, de bout en bout**, et vérifie qu'aucun bandeau/modal ne masque le formulaire. Si un élément
> bloquant est visible, clique-le via `POST /click {"x":...,"y":...}` (coordonnées lues sur le screenshot),
> reconfirme avec `/state`, puis seulement lance `/login`. Réflexe valable à **toute étape ambiguë**.

```bash
## Inspecter l'état réel AVANT de remplir (détection sans action)
STATE=$(curl -sf http://localhost:$PORT/state)
echo "$STATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print('state=',d.get('state'),'screenshot=',d.get('screenshot'))"
## → Lire le screenshot retourné DIRECTEMENT. Si bannière/modal bloquant : POST /click puis re-/state.
```

```bash
for i in $(seq 1 10); do
  RESULT=$(curl -sf -X POST http://localhost:$PORT/login)
  STATUS=$(echo $RESULT | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
  echo "→ status=$STATUS"

  case $STATUS in
    logged_in)
      echo "✅ Connecté"
      break
      ;;
    action_taken)
      ## Consent dismissé ou formulaire soumis — reboucler immédiatement
      ;;
    captcha_waiting)
      ## Challenge image détecté — LIRE les images TOI-MÊME (screenshot global + window_*.png),
      ## vite et en une passe (section 3), puis /captcha/respond et reboucler.
      echo $RESULT | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('instructions',''))"
      ;;
    2fa_required)
      ## NE PAS conclure directement — lire le `reason` ET inspecter le `screenshot` TOI-MÊME (section 4).
      echo "🔍 2fa_required reçu — vérification du reason avant de conclure (section 4)"
      break
      ;;
    error)
      echo "❌ Erreur : $(echo $RESULT | python3 -c 'import sys,json; print(json.load(sys.stdin).get("reason",""))')"
      echo $RESULT | python3 -m json.tool
      break
      ;;
  esac
done
```

| `status` | Signification | Action |
|----------|--------------|--------|
| `logged_in` | ✅ Connecté | Terminer |
| `action_taken` | Consent dismissé ou formulaire soumis | Rappeler `/login` |
| `captcha_waiting` | Challenge image en attente | Appeler `/captcha/respond` puis reboucler |
| `2fa_required` | Voir section 4 — **ne pas conclure trop vite**, lire le `reason` | Vérifier le `reason` avant d'agir |
| `error` | Erreur terminale | Lire `reason` (`login_rejected`, `captcha_*`, ...) |

### 3 — Résoudre un CAPTCHA image (status=captcha_waiting)

La réponse de `/login` contient déjà les infos du challenge :

```json
{
  "status": "captcha_waiting",
  "screenshot": "/tmp/.../cps_captcha_....png",
  "n": 16,
  "grid_cols": 4,
  "windows": [
    {"row":0,"col":0,"path":"/tmp/captcha_windows_PID/win_0_0.png","indices":[0,1,4,5],"layout":"TL=0, TR=1, BL=4, BR=5"},
    ...
  ],
  "refresh_available": true,
  "instructions": "..."
}
```

**Protocole — lecture directe, vite (tu es déjà dans le contexte isolé du profil) :**

> ⚠️ **La session reCAPTCHA expire en ~2-3 min.** Lis les images **TOI-MÊME, en une seule passe
> parallèle** (un seul message, plusieurs lectures d'image), puis réponds **immédiatement**.
> ⛔ **Ne re-spawne PAS un sous-agent par round** : le runtime n'a pas d'outil de sous-agent fiable,
> tu perdrais ~1 min/round (ToolSearch + install SDK + Bash) et la session expirerait avant la fin.
> L'isolation par profil te protège déjà de l'accumulation d'images dans la session parente.

1. **Lire en une seule passe** : le `screenshot` global **et** toutes les `windows[].path` dans le
   **même message**.
   - Le screenshot global donne l'objet demandé (titre bleu du challenge).
   - Chaque fenêtre = 4 cases adjacentes (TL/TR/BL/BR), avec ses `indices`/`layout`.
   - **Règle :** une case est positive si l'objet y apparaît, **même partiellement**.

2. **Répondre immédiatement** via `/captcha/respond` :

```bash
## Indices des cases contenant l'objet
curl -X POST http://localhost:$PORT/captcha/respond \
  -H "Content-Type: application/json" \
  -d '{"indices": [0, 2, 5]}'

## Aucune case à sélectionner — soumettre directement
curl -X POST http://localhost:$PORT/captcha/respond \
  -H "Content-Type: application/json" \
  -d '{"indices": []}'

## Images illisibles — charger de nouvelles images (bouton ↺)
curl -X POST http://localhost:$PORT/captcha/respond \
  -H "Content-Type: application/json" \
  -d '{"refresh": true}'
```

Interprétation de la réponse :
- `{"status": "captcha_solved"}` → rappeler `/login` pour continuer la machine à états
- `{"status": "captcha_waiting", "windows": [...]}` → **nouveau round : relis directement les nouvelles
  images** (étape 1, même passe parallèle) et re-réponds via `/captcha/respond`

### 4 — 2FA (status=2fa_required)

> ⚠️ **Cas rare** — ne jamais conclure à une 2FA par paresse. La majorité des "checkpoints"
> sont de simples reCAPTCHA solubles (section 3). Le serveur ne renvoie ce statut qu'après avoir
> tenté de résoudre un reCAPTCHA et vérifié la présence de marqueurs explicites.

#### 4.a — Tentative AUTOMATIQUE : code 2FA par email (AVANT toute conclusion manuelle)

Beaucoup de 2FA envoient un **code par email** sur une boîte qu'on contrôle (`json_data.username`
+ `json_data.imap_app_password`, cf. `notebooks/dev_scrapper/2fa_email_imap_setup.md`). Tenter de le
récupérer et de le soumettre **avant** de conclure à une 2FA manuelle :

```bash
CODE=$(python /workspace/scripts/monitoring/fetch_2fa_code.py $APIKEY_ID --wait 60 --max-age 180)
if [ -n "$CODE" ]; then
  echo "✅ code email récupéré : $CODE — soumission via /code"
  curl -s -X POST http://localhost:$PORT/code \
    -H "Content-Type: application/json" -d "{\"code\": \"$CODE\"}"
  ## puis reboucler /login (section 2) jusqu'à logged_in
fi
```

- `--wait 60` : le mail arrive en quelques secondes → poll jusqu'à 60 s.
- `--max-age 180` : n'utilise qu'un code **fraîchement reçu** (déclenché par CETTE tentative) ; un vieux
  code d'une tentative précédente est ignoré.
- **Code trouvé + soumis** → reboucler `/login`. Si `logged_in` → succès (réactivation par l'orchestrateur).
- **Aucun code (`$CODE` vide, exit 1)** → ce n'est PAS une 2FA email (SMS / appli d'authentification /
  approbation depuis un autre appareil) → passer à **4.b** (manuel).

> ⛔ Ne touche toujours pas au statut DB (réactivation = orchestrateur après `logged_in`). En contexte
> sous-agent `monitor-scrapper`, idem (cf. section « Marquer disactive »).

#### 4.b — 2FA NON-email → intervention manuelle

Lire le champ `reason` :

| `reason` | Action |
|----------|--------|
| `manual_2fa_marker_detected` | 2FA confirmée (texte "enter the code", champ SMS, approbation depuis un autre appareil…) → mentionner dans le rapport, passer au compte suivant |
| `no_recaptcha_and_no_marker_after_retries` | **Ne pas conclure à une 2FA.** Lire le `screenshot` **toi-même** pour identifier ce qui bloque : reCAPTCHA pas encore chargé, bannière/consent non géré, autre challenge… Rappeler `/login` ou appeler `POST /captcha/click-checkbox` si un reCAPTCHA est visible. Mentionner 2FA dans le rapport **seulement** si aucune action automatisable n'est possible. |

Si la 2FA est confirmée, **mentionner dans le rapport** et passer au compte suivant.

### 5 — Shutdown propre

```bash
curl -X POST http://localhost:$PORT/shutdown
```

---

## Référence rapide — Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Serveur vivant ? |
| `GET /state` | État courant sans agir (screenshot inclus) |
| `POST /login` | Une étape de la machine à états → status |
| `POST /captcha/respond` | `{"indices":[...]}` ou `{"refresh":true}` |
| `POST /code` | `{"code":"123456"}` (+ `checkpoint_url` optionnel) → saisit le code OTP/2FA et soumet |
| `POST /screenshot` | `{"label":"..."}` → `{"path":"..."}` |
| `POST /click` | `{"x":100,"y":200}` → clic humain (Bézier) |
| `POST /shutdown` | Fermer Chrome + Xvfb |

---

## Marquer disactive si reconnexion impossible

> ⚠️ **Contexte `monitor-scrapper` (sous-agent) : NE FAIS PAS cette désactivation.** Quand tu es spawné
> par `monitor-scrapper` (ÉTAPE 3b-Chrome), **le statut DB est géré par l'orchestrateur** : la clé a déjà
> été passée `disactive` au triage (ÉTAPE 3b-Chrome Étape 1-bis) et ta consigne est de **ne jamais toucher
> au statut**. Tu te contentes de retourner `logged_in: false` + `reason`. **Cette section ne vaut que pour
> un usage standalone** de la commande (hors monitor-scrapper).

**Ne marquer disactive qu'après avoir tenté** et obtenu une erreur terminale (`error` avec `reason=login_rejected` ou `reason=captcha_*`).

> ⚠️ **Prérequis strict :** le compte ne peut être marqué `disactive` que si **toutes** les conditions suivantes sont réunies :
> 1. La commande `/reconnect-chrome-profile` a été utilisée (pas un script Playwright ad hoc)
> 2. Un screenshot a été pris et lu visuellement pour confirmer la nature du blocage
> 3. Pour les cas `2fa_required` : le `reason` vaut `manual_2fa_marker_detected` — i.e. le serveur a trouvé un marqueur explicite de code SMS/email (jamais déduire une 2FA depuis l'état de `tokens_cache` seul)
>
> Un `tokens_cache` vide, un consent wall RGPD, ou un reCAPTCHA ne sont **pas** des raisons valides de marquer `disactive`.

```python
APIKEY_ID = int("APIKEY_ID")  # ← remplacer

from gcpOpsci.db import get_db
from gcpOpsci.models import ApiKey

db = next(get_db())
key = db.query(ApiKey).filter_by(id=APIKEY_ID).first()
other_active = db.query(ApiKey).filter(
    ApiKey.platform == key.platform,
    ApiKey.status == 'active',
    ApiKey.id != APIKEY_ID,
).count()
if other_active > 0:
    key.status = 'disactive'
    db.commit()
    print(f"ApiKey #{APIKEY_ID} → disactive ({other_active} autre(s) active(s))")
else:
    print(f"⚠️  Seule ApiKey active pour {key.platform} — ne pas désactiver")
db.close()
```
