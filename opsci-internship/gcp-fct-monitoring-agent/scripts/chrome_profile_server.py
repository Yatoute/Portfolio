#!/usr/bin/env python3
"""
Chrome Profile Server — daemon HTTP persistant, tout-en-un.

Chrome + Xvfb démarrent une seule fois. Le serveur gère lui-même la machine à états
complète (DB, consent, login, CAPTCHA). L'agent n'a besoin que de curl.

Usage:
    python chrome_profile_server.py --apikey-id 24 [--port 8231] [--screenshot-dir /tmp/cpm_24]
    # Port écrit dans /tmp/cpm_server_<apikey_id>.port

Workflow agent :
    curl -X POST localhost:PORT/login          # appeler en boucle
    # si status=captcha_waiting : lire les window PNG, puis :
    curl -X POST localhost:PORT/captcha/respond -d '{"indices":[0,2,5]}'
    # reprendre la boucle /login

Endpoints :
    GET  /health
    GET  /state                → état courant de la page + screenshot
    POST /login                → une étape de la machine à états → status
    POST /captcha/respond      → {"indices":[...]} ou {"refresh":true}
    POST /screenshot           → {"label":"..."} → {"path":"..."}
    POST /click                → {"x":100,"y":200}
    POST /shutdown
"""

import asyncio
import json
import logging
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel


# ─── Config plateformes ────────────────────────────────────────────────────────

GCS_BUCKET           = "demo-project-scrapper"
MOUNT_BASE           = Path(tempfile.gettempdir()) / "sds_gcs_mounts"
CLOUD_RUN_MOUNT_BASE = Path("/chrome-profile")

_PLATFORM_CONFIG = {
    "Facebook": {
        "home_url":                "https://www.facebook.com/",
        "login_url":               "https://www.facebook.com/login",
        "auth_cookie":             "c_user",
        "login_path_keywords":     ["checkpoint", "two_step", "approvals", "login/device", "auth_platform"],
        "cookie_consent_keywords": [
            "Allow all cookies", "Autoriser tous les cookies",
            "Accept all", "Tout accepter",
        ],
        "email_locator":    ("css", "input[name='email'], input[id='email']"),
        "password_locator": ("css", "input[type='password']"),
    },
    "LinkedIn": {
        "home_url":            "https://www.linkedin.com/feed/",
        "login_url_en":        "https://www.linkedin.com/login/en",
        "auth_cookie":         "li_at",
        "login_path_keywords": ["checkpoint", "authwall", "two-step-challenge"],
        ## input#username n'existe plus (ID React dynamique depuis ~2026-06).
        ## :visible filtre le doublon caché React qui apparaît en premier dans le DOM.
        "email_locator":    ("css", "input[type='email']:visible, input#username"),
        "password_locator": ("role", "textbox", "Password"),
    },
    "Truth Social": {
        "home_url":            "https://truthsocial.com/",
        "login_url":           "https://truthsocial.com/login",
        "auth_cookie":         None,
        "session_cookies":     ["_session_id", "_mastodon_session"],
        "login_path_keywords": ["checkpoint", "two_step"],
        "email_locator":    ("css", "input[name='username']"),
        "password_locator": ("css", "input[name='password']"),
    },
    "Instagram": {
        "home_url":            "https://www.instagram.com/",
        "login_url":           "https://www.instagram.com/accounts/login/",
        "auth_cookie":         "sessionid",
        "login_path_keywords": ["challenge", "checkpoint", "auth_platform", "codeentry", "two_factor"],
        "cookie_consent_keywords": [
            "Allow all cookies", "Autoriser tous les cookies",
            "Accept all", "Tout accepter",
            "Allow essential and optional cookies",
        ],
        ## Instagram React SPA : le champ username a name='email' (placeholder "Mobile number, username or email")
        ## Fallback sur aria-label et autocomplete pour robustesse
        "email_locator":    ("css", "input[name='email'], input[name='username'], input[aria-label*='username' i], input[aria-label*='email' i], input[autocomplete='username']"),
        "password_locator": ("css", "input[name='password'], input[type='password']"),
    },
}

_CAPTCHA_DETECT_JS = """() => {
    const hasArkose = !!document.querySelector(
        'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"], [id*="arkose"], [class*="FunCaptcha"], #fc-token'
    );
    const hasRecaptcha = !!document.querySelector(
        '.g-recaptcha, iframe[src*="recaptcha"], [data-sitekey]'
    );
    const bodyText = document.body?.innerText || '';
    const hasChallengeText = /security.?check|vérification de sécurité|confirm.{0,30}identity|click.{0,20}all|cliquez sur|défi|challenge|puzzle/i.test(bodyText);
    const hasImageGrid = document.querySelectorAll(
        '[class*="challenge"] img, [class*="captcha"] img, [class*="tile"] img'
    ).length >= 4;
    if (hasArkose)        return "arkose";
    if (hasRecaptcha)     return "recaptcha";
    if (hasImageGrid)     return "image_grid";
    if (hasChallengeText) return "text_challenge";
    return null;
}"""

## Marqueurs explicites d'une 2FA manuelle (code SMS, appli d'authentification, approbation device)
## — sert à ne PAS confondre un simple checkpoint reCAPTCHA lent à charger avec une vraie 2FA
_2FA_MARKERS_JS = """() => {
    const txt = (document.body?.innerText || '').toLowerCase();
    const phrases = [
        'enter the code', 'entrez le code', 'code de confirmation',
        'we sent a notification', 'nous avons envoyé une notification',
        'approve this login', 'approuvez cette connexion',
        'check your phone', 'vérifiez votre téléphone',
        'authentication app', "application d'authentification",
        'two-factor', 'deux facteurs', '2fa',
        'security code', 'code de sécurité',
        'verification code', 'code de vérification',
        'one-time code', 'code à usage unique',
    ];
    if (phrases.some(p => txt.includes(p))) return true;
    return !!document.querySelector(
        'input[name*="code" i], input[autocomplete="one-time-code"], input[id*="approvals_code" i]'
    );
}"""

LOGIN_STATES = {
    "already_logged_in": "✅ Session active",
    "consent_modal":     "🍪 Modal cookie consent",
    "login_form":        "📝 Formulaire de connexion",
    "2fa":               "📱 2FA / checkpoint requis",
    "captcha":           "🔒 CAPTCHA / challenge visuel",
    "post_login_error":  "❌ Erreur après soumission",
    "unknown":           "❓ État non reconnu",
}


# ─── État global du daemon ────────────────────────────────────────────────────

_B: dict = {
    "ctx":            None,
    "page":           None,
    "cfg":            None,
    "platform":       None,
    "apikey_id":      None,
    "username":       None,
    "password":       None,
    "screenshot_dir": "/tmp",
    "xvfb_proc":      None,
    "pw":             None,
    "prev_login_state": None,   # anti-boucle infinie
    "twofa_retry_count": 0,     # tentatives de détection reCAPTCHA avant de conclure 2FA
    "demo_url":       None,     # mode démo sans DB/GCS
}


# ─── GCS helpers ──────────────────────────────────────────────────────────────

def _mount_point(gcs_prefix: str) -> Path:
    cloud_run_path = CLOUD_RUN_MOUNT_BASE / gcs_prefix.strip("/")
    if cloud_run_path.is_dir():
        return cloud_run_path
    name = gcs_prefix.strip("/").replace("/", "_")
    return MOUNT_BASE / name


def _is_mounted(mount_dir: Path) -> bool:
    return str(mount_dir) in subprocess.run(["mount"], capture_output=True, text=True).stdout


def _mount_gcs(mount_dir: Path, gcs_prefix: str) -> None:
    if mount_dir == CLOUD_RUN_MOUNT_BASE / gcs_prefix.strip("/"):
        return
    if _is_mounted(mount_dir):
        return
    mount_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["gcsfuse", "--implicit-dirs", f"--only-dir={gcs_prefix}", GCS_BUCKET, str(mount_dir)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcsfuse échoué : {result.stderr.strip()}")
    logging.info(f"gcsfuse monté : gs://{GCS_BUCKET}/{gcs_prefix}/ → {mount_dir}")


# ─── Helpers Playwright ────────────────────────────────────────────────────────

def _loc(page, spec):
    if spec is None:
        return None
    if spec[0] == "role":
        return page.get_by_role(spec[1], name=spec[2])
    return page.locator(spec[1])


async def _screenshot(label: str) -> str:
    path = str(Path(_B["screenshot_dir"]) / f"cps_{label}_{int(time.time())}.png")
    await _B["page"].screenshot(path=path, full_page=False)
    logging.info(f"📸 [{label}] {path}")
    return path


async def _detect_state() -> dict:
    page = _B["page"]
    cfg  = _B["cfg"]
    ctx  = _B["ctx"]
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    url  = page.url
    shot = await _screenshot("detect")
    base = {"url": url, "screenshot": shot}

    auth_cookie = cfg.get("auth_cookie")
    if auth_cookie:
        try:
            c = next((c for c in await ctx.cookies() if c["name"] == auth_cookie), None)
            if c:
                return {**base, "state": "already_logged_in", "auth_value": c["value"]}
        except Exception:
            pass

    if any(x in url for x in cfg.get("login_path_keywords", [])):
        ## Sur une URL checkpoint, toujours router vers "2fa" même si _CAPTCHA_DETECT_JS
        ## détecte un reCAPTCHA — le handler "2fa" sait cliquer le checkbox ET résoudre la grille.
        ## Retourner "captcha" ici court-circuitait le handler et renvoyait une erreur terminale.
        return {**base, "state": "2fa"}

    ## Détection consent robuste (générique, multi-plateforme) : matching insensible à la casse,
    ## normalisé (espaces) et par SOUS-CHAÎNE, restreint aux éléments cliquables. Plus tolérant que
    ## l'égalité de texte exacte — attrape les variantes de casse/espaces/libellé d'un bouton "Accepter".
    kws = [k.lower() for k in cfg.get("cookie_consent_keywords", [])]
    if kws:
        try:
            bbox = await page.evaluate("""(kws) => {
                const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const sel = 'button, [role=button], a[role=button], input[type=submit], input[type=button]';
                for (const el of document.querySelectorAll(sel)) {
                    const t = norm(el.textContent) || norm(el.value) || norm(el.getAttribute('aria-label'));
                    if (!t) continue;
                    if (kws.some(k => t.includes(k))) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: r.left + r.width / 2, y: r.top + r.height / 2, kw: t.slice(0, 40)};
                    }
                }
                return null;
            }""", kws)
        except Exception:
            bbox = None
        if bbox:
            return {**base, "state": "consent_modal", "consent_bbox": bbox, "consent_kw": bbox["kw"]}

    try:
        captcha_type = await page.evaluate(_CAPTCHA_DETECT_JS)
    except Exception:
        captcha_type = None
    if captcha_type:
        return {**base, "state": "captcha", "captcha_type": captcha_type}

    try:
        pwd_loc = _loc(page, cfg.get("password_locator"))
        if pwd_loc and await pwd_loc.count():
            return {**base, "state": "login_form"}
    except Exception:
        pass

    if "/login" in url:
        return {**base, "state": "post_login_error"}

    return {**base, "state": "unknown"}


async def _human_move_and_click(x: float, y: float):
    page = _B["page"]
    sx = random.uniform(100, 600)
    sy = random.uniform(100, 500)
    cp1x = sx + random.uniform(-120, 120)
    cp1y = sy + random.uniform(-80,   80)
    cp2x = x  + random.uniform(-60,   60)
    cp2y = y  + random.uniform(-40,   40)
    steps = random.randint(18, 30)
    for i in range(steps + 1):
        t  = i / steps
        t2, t3, u = t*t, t*t*t, 1-t
        u2, u3    = u*u, u*u*u
        bx = u3*sx + 3*u2*t*cp1x + 3*u*t2*cp2x + t3*x
        by = u3*sy + 3*u2*t*cp1y + 3*u*t2*cp2y + t3*y
        await page.mouse.move(bx, by)
        await asyncio.sleep(random.uniform(0.004, 0.016))
    await asyncio.sleep(random.uniform(0.06, 0.18))
    await page.mouse.click(x, y)


async def _get_bframe():
    ## `document.querySelectorAll('iframe')` depuis la page principale ne trouve pas le bframe
    ## quand reCAPTCHA est imbriqué dans un iframe wrapper cross-origin (ex: fbsbx.com sur Facebook).
    ## `frame.frame_element()` traverse l'arbre de frames de Playwright et fonctionne quel que
    ## soit le niveau d'imbrication / l'origine.
    for frame in _B["page"].frames:
        if "bframe" in frame.url and frame.url.startswith("http"):
            try:
                el  = await frame.frame_element()
                box = await el.bounding_box()
                if box and box["height"] > 50 and box["y"] > -200:
                    return frame
            except Exception:
                pass
    return None


async def _get_bframe_offset() -> dict:
    for frame in _B["page"].frames:
        if "bframe" in frame.url and frame.url.startswith("http"):
            try:
                el  = await frame.frame_element()
                box = await el.bounding_box()
                if box:
                    return {"left": box["x"], "top": box["y"]}
            except Exception:
                pass
    return {"left": 0, "top": 0}


def _detect_grid_cols(n: int) -> int:
    if n == 9:  return 3
    if n == 16: return 4
    return int(math.sqrt(n))


def _build_windows(tiles_info: list, grid_cols: int) -> list:
    grid_rows = len(tiles_info) // grid_cols
    windows   = []
    for r in range(grid_rows - 1):
        for c in range(grid_cols - 1):
            idxs = [
                r       * grid_cols + c,
                r       * grid_cols + c + 1,
                (r + 1) * grid_cols + c,
                (r + 1) * grid_cols + c + 1,
            ]
            windows.append({"row": r, "col": c, "indices": idxs,
                             "tiles": [tiles_info[i] for i in idxs]})
    return windows


async def _crop_windows(bframe_off: dict, windows: list) -> list:
    win_dir = Path(f"/tmp/captcha_windows_{os.getpid()}")
    win_dir.mkdir(exist_ok=True)
    result  = []
    for w in windows:
        ts, idxs, r, c = w["tiles"], w["indices"], w["row"], w["col"]
        x0   = round(bframe_off["left"] + ts[0]["x"] - ts[0]["w"] // 2)
        y0   = round(bframe_off["top"]  + ts[0]["y"] - ts[0]["h"] // 2)
        path = str(win_dir / f"win_{r}_{c}.png")
        try:
            await _B["page"].screenshot(path=path, clip={
                "x": max(0, x0), "y": max(0, y0),
                "width":  ts[0]["w"] * 2,
                "height": ts[0]["h"] * 2,
            })
            result.append({
                "path":    path,
                "indices": idxs,
                "row":     r,
                "col":     c,
                "layout":  f"TL={idxs[0]}, TR={idxs[1]}, BL={idxs[2]}, BR={idxs[3]}",
            })
        except Exception as e:
            logging.warning(f"Crop [{r},{c}] échoué : {e}")
    logging.info(f"{len(result)} fenêtres 2×2 → {win_dir}")
    return result


async def _setup_captcha_windows() -> Optional[dict]:
    """Attend les images + croppe les fenêtres. Retourne None si pas de bframe."""
    bframe = await _get_bframe()
    if not bframe:
        return None

    try:
        await bframe.wait_for_function("""() => {
            const imgs = document.querySelectorAll('img');
            return imgs.length > 0 && Array.from(imgs).every(
                img => img.complete && img.naturalWidth > 0
            );
        }""", timeout=8000)
    except Exception:
        await _B["page"].wait_for_timeout(1500)

    tiles_info = await bframe.evaluate("""() => {
        const tiles = document.querySelectorAll(
            'td.rc-imageselect-tile, .rc-imageselect-tile, [class*="imageselect-tile"]'
        );
        return Array.from(tiles).map((t, i) => {
            const r = t.getBoundingClientRect();
            return {i, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
                    w: Math.round(r.width), h: Math.round(r.height)};
        });
    }""")
    if not tiles_info:
        return None

    shot       = await _screenshot("captcha")
    grid_cols  = _detect_grid_cols(len(tiles_info))
    bframe_off = await _get_bframe_offset()
    win_data   = _build_windows(tiles_info, grid_cols)
    windows    = await _crop_windows(bframe_off, win_data)

    refresh_available = False
    try:
        rb = await bframe.evaluate("""() => {
            const btn = document.querySelector(
                '#recaptcha-reload-button, .rc-imageselect-reload, [id*="reload"]'
            );
            return btn ? {visible: btn.getBoundingClientRect().width > 0} : null;
        }""")
        refresh_available = bool(rb and rb.get("visible"))
    except Exception:
        pass

    win_lines = "\n".join(
        f"  [{w['row']},{w['col']}] {w['path']}  →  {w['layout']}"
        for w in windows
    )
    n = len(tiles_info)
    return {
        "screenshot":        shot,
        "n":                 n,
        "grid_cols":         grid_cols,
        "windows":           windows,
        "refresh_available": refresh_available,
        "instructions": (
            f"Challenge reCAPTCHA — grille ({n} cases, 0 à {n-1}).\n"
            "\n"
            "⚠️ SESSION ~2-3 MIN — NE LIS RIEN SÉQUENTIELLEMENT. Dans UN SEUL message,\n"
            f"appelle ton outil de lecture d'image {len(windows) + 1} FOIS EN PARALLÈLE\n"
            "(plusieurs blocs tool_use dans la même réponse) pour charger d'un coup :\n"
            f"  1 screenshot global + {len(windows)} fenêtres listées ci-dessous.\n"
            "N'attends PAS le résultat de l'une pour lancer la lecture de la suivante.\n"
            "\n"
            f"  [GLOBAL] {shot}  →  identifie l'OBJET demandé (titre bleu)\n"
            f"{win_lines}\n"
            "\n"
            "RÈGLE : case POSITIVE si l'objet y est présent, même partiellement.\n"
            "Une fois TOUTES les images lues → fais l'union des indices positifs et réponds\n"
            "immédiatement (ne relis rien, n'hésite pas) :\n"
            + ('  - "refresh" si les images sont illisibles\n' if refresh_available else "")
            + f"  - Écris le JSON dans /tmp/captcha_response_{os.getpid()}.json, ou\n"
            "  - Appelle directement POST /captcha/respond avec {\"indices\": [...]}"
        ),
    }


# ─── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup()
    yield
    await _shutdown()


async def _startup():
    ## Mode démo : Chrome avec profil temporaire, sans DB ni GCS
    if _B.get("demo_url"):
        import tempfile as _tmp
        tmp_profile = _tmp.mkdtemp(prefix="cpm_demo_")
        xvfb_proc = None
        try:
            xvfb_proc = subprocess.Popen(
                ["Xvfb", ":99", "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            os.environ["DISPLAY"] = ":99"
            logging.info("Xvfb démarré (mode démo)")
        except FileNotFoundError:
            logging.warning("Xvfb absent — headless natif")
        pw  = await async_playwright().start()
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=tmp_profile,
            headless=xvfb_proc is None,
            slow_mo=0,
            args=[
                "--no-sandbox", "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage", "--use-gl=swiftshader",
                "--ignore-gpu-blocklist", "--enable-gpu",
            ],
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_default_args=["--enable-automation"],
            device_scale_factor=2,
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR', 'fr', 'en-US', 'en']});
            window.chrome = {runtime: {}};
        """)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(_B["demo_url"], timeout=30000, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        _B.update(ctx=ctx, page=page, cfg={}, pw=pw, xvfb_proc=xvfb_proc)
        logging.info(f"✅ Serveur prêt — mode démo → {_B['demo_url']}")
        return

    cfg       = _PLATFORM_CONFIG[_B["platform"]]
    gcs_path  = _B["gcs_profile_path"]
    mount_dir = _mount_point(gcs_path)
    _mount_gcs(mount_dir, gcs_path)

    singleton = mount_dir / "SingletonLock"
    if singleton.exists():
        try:
            singleton.unlink()
        except Exception:
            pass

    xvfb_proc = None
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        os.environ["DISPLAY"] = ":99"
        logging.info("Xvfb démarré")
    except FileNotFoundError:
        logging.warning("Xvfb absent — Chrome en mode headless natif")

    pw  = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(mount_dir),
        headless=xvfb_proc is None,
        slow_mo=0,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--password-store=basic",
            "--disable-dev-shm-usage",
            "--use-gl=swiftshader",
            "--ignore-gpu-blocklist",
            "--enable-gpu",
        ],
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        ignore_default_args=["--enable-automation"],
        device_scale_factor=2,
    )
    await ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR', 'fr', 'en-US', 'en']});
        window.chrome = {runtime: {}};
    """)

    page      = ctx.pages[0] if ctx.pages else await ctx.new_page()
    login_url = cfg.get("login_url_en") or cfg.get("login_url") or ""
    await page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    _B.update(ctx=ctx, page=page, cfg=cfg, pw=pw, xvfb_proc=xvfb_proc)
    logging.info(f"✅ Serveur prêt — {_B['platform']} #{_B['apikey_id']}")


async def _shutdown():
    if _B["ctx"]:
        try:
            await _B["ctx"].close()
        except Exception:
            pass
    if _B["pw"]:
        try:
            await _B["pw"].stop()
        except Exception:
            pass
    if _B["xvfb_proc"]:
        _B["xvfb_proc"].terminate()
    Path(f"/tmp/cpm_server_{_B.get('apikey_id') or 'demo'}.port").unlink(missing_ok=True)
    logging.info("Serveur arrêté")


# ─── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "platform": _B["platform"], "apikey_id": _B["apikey_id"]}


@app.get("/state")
async def get_state():
    try:
        info = await _detect_state()
        return {**info, "state_label": LOGIN_STATES.get(info["state"], info["state"])}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/login")
async def login():
    """Une étape de la machine à états. Appeler en boucle jusqu'à status='logged_in'.

    Statuts possibles :
      logged_in       → connecté, auth_value disponible
      action_taken    → action effectuée (consent/fill), rappeler /login
      captcha_waiting → challenge image en attente, appeler /captcha/respond puis /login
      2fa_required    → 2FA manuelle confirmée (reason=manual_2fa_marker_detected)
                        ou non résolue après plusieurs tentatives reCAPTCHA
                        (reason=no_recaptcha_and_no_marker_after_retries — rare,
                        à re-tester avant de conclure à une vraie 2FA)
      error           → erreur terminale, reason dans la réponse
    """
    try:
        page = _B["page"]
        cfg  = _B["cfg"]
        info = await _detect_state()
        s    = info["state"]
        logging.info(f"🔵 [{_B['platform']}] /login → {s} — {info.get('screenshot')}")

        if s == "already_logged_in":
            _B["prev_login_state"] = None
            return {
                "status":      "logged_in",
                "auth_value":  info.get("auth_value"),
                "screenshot":  info.get("screenshot"),
                "state_label": LOGIN_STATES["already_logged_in"],
            }

        ## Détection de boucle (même état deux fois de suite sur login_form)
        if s == "login_form" and _B["prev_login_state"] == "login_form":
            return {"status": "error", "reason": "login_rejected",
                    "screenshot": info.get("screenshot")}
        _B["prev_login_state"] = s

        ## Reset du compteur de retries 2FA dès qu'on quitte cet état
        if s != "2fa":
            _B["twofa_retry_count"] = 0

        if s == "consent_modal":
            bbox = info.get("consent_bbox", {})
            await page.mouse.click(bbox.get("x", 0), bbox.get("y", 0))
            await page.wait_for_timeout(1500)
            return {"status": "action_taken", "action": "consent_dismissed",
                    "screenshot": info.get("screenshot")}

        if s == "login_form":
            username = _B.get("username")
            password = _B.get("password")
            if not username or not password:
                return {"status": "error", "reason": "no_credentials"}

            email_loc = _loc(page, cfg.get("email_locator"))
            if email_loc:
                cnt = await email_loc.count()
                if cnt > 0:
                    try:
                        ## .first évite l'erreur strict-mode si le sélecteur matche plusieurs éléments
                        ## (ex: LinkedIn avec champ visible + doublon caché React)
                        target = email_loc.first if cnt > 1 else email_loc
                        if not await target.is_disabled():
                            await target.fill(username)
                    except Exception:
                        pass

            pwd_loc = _loc(page, cfg.get("password_locator"))
            if not pwd_loc or not await pwd_loc.count():
                return {"status": "error", "reason": "password_field_not_found",
                        "screenshot": info.get("screenshot")}

            await pwd_loc.fill(password)
            try:
                await pwd_loc.press("Enter")
            except Exception:
                pass
            try:
                await page.wait_for_url(
                    lambda url: cfg.get("login_url", "/login").rstrip("/") not in url.rstrip("/"),
                    timeout=15000,
                )
            except Exception:
                pass
            return {"status": "action_taken", "action": "form_submitted",
                    "screenshot": info.get("screenshot")}

        if s == "2fa":
            ## Tenter le clic automatique sur le checkbox reCAPTCHA
            await page.wait_for_timeout(3000)
            recaptcha_frame = None
            for _ in range(6):
                await page.wait_for_timeout(2000)
                for frame in page.frames:
                    if "recaptcha" in frame.url or "captcha" in frame.url:
                        recaptcha_frame = frame
                        break
                if recaptcha_frame:
                    break

            if recaptcha_frame:
                try:
                    await recaptcha_frame.wait_for_load_state("domcontentloaded", timeout=8000)
                    cb = await recaptcha_frame.evaluate("""() => {
                        const sel = ['#recaptcha-anchor', '.recaptcha-checkbox',
                                     '[role="checkbox"]', '.rc-anchor-checkbox'];
                        for (const s of sel) {
                            const el = document.querySelector(s);
                            if (el) { const r = el.getBoundingClientRect();
                                      return {x: r.left+r.width/2, y: r.top+r.height/2, sel: s}; }
                        }
                        return null;
                    }""")
                    if cb:
                        await recaptcha_frame.click(cb["sel"], timeout=5000)
                    else:
                        await recaptcha_frame.locator("body").click(
                            position={"x": 24, "y": 37}, timeout=5000)
                except Exception:
                    try:
                        fr = await page.evaluate("""() => {
                            for (const f of document.querySelectorAll('iframe')) {
                                if (f.src && f.src.includes('recaptcha')) {
                                    const r = f.getBoundingClientRect();
                                    return {left: r.left, top: r.top, w: f.offsetWidth, h: f.offsetHeight};
                                }
                            }
                            return null;
                        }""")
                        if fr and fr["w"] > 0:
                            await page.mouse.click(
                                fr["left"] + min(24, fr["w"] * 0.1),
                                fr["top"]  + fr["h"] / 2,
                            )
                    except Exception:
                        pass

                await page.wait_for_timeout(4000)
                captcha_info = await _setup_captcha_windows()
                if captcha_info:
                    _B["twofa_retry_count"] = 0
                    return {"status": "captcha_waiting", **captcha_info}

                ## Le clic a pu valider directement sans challenge visuel (passage silencieux)
                ## → re-détecter l'état réel plutôt que de conclure trop vite à une 2FA
                new_info = await _detect_state()
                if new_info["state"] != "2fa":
                    _B["twofa_retry_count"] = 0
                    return {"status": "action_taken", "action": "recaptcha_passed",
                            "screenshot": new_info.get("screenshot")}

            ## Vérifier la présence de marqueurs EXPLICITES de 2FA manuelle
            ## (code SMS, appli d'authentification, approbation depuis un autre appareil)
            try:
                has_2fa_marker = await page.evaluate(_2FA_MARKERS_JS)
            except Exception:
                has_2fa_marker = False
            if has_2fa_marker:
                _B["twofa_retry_count"] = 0
                return {"status": "2fa_required", "reason": "manual_2fa_marker_detected",
                        "screenshot": info.get("screenshot"), "url": info.get("url")}

            ## Ni reCAPTCHA trouvé, ni marqueur 2FA — la page charge peut-être encore :
            ## reboucler quelques fois avant de conclure (anti faux-positif "2FA")
            _B["twofa_retry_count"] += 1
            if _B["twofa_retry_count"] < 3:
                return {"status": "action_taken", "action": "retry_checkpoint_detection",
                        "screenshot": info.get("screenshot")}

            _B["twofa_retry_count"] = 0
            return {"status": "2fa_required", "reason": "no_recaptcha_and_no_marker_after_retries",
                    "screenshot": info.get("screenshot"), "url": info.get("url")}

        if s == "captcha":
            return {"status": "error", "reason": f"captcha_{info.get('captcha_type','blocked')}",
                    "screenshot": info.get("screenshot")}

        return {"status": "error", "reason": s, "screenshot": info.get("screenshot")}

    except Exception as e:
        raise HTTPException(500, str(e))


class CaptchaRespondReq(BaseModel):
    indices: Optional[list] = None
    refresh: bool = False

@app.post("/captcha/respond")
async def captcha_respond(req: CaptchaRespondReq):
    """Clique les tiles sélectionnées (ou ↺), clique VERIFY, retourne si un nouveau round attend."""
    try:
        page   = _B["page"]
        bframe = await _get_bframe()

        if req.refresh:
            if not bframe:
                raise HTTPException(404, "bframe introuvable pour refresh")
            rb_pos = await bframe.evaluate("""() => {
                const btn = document.querySelector(
                    '#recaptcha-reload-button, .rc-imageselect-reload, [id*="reload"]'
                );
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
            }""")
            if rb_pos:
                off = await _get_bframe_offset()
                await _human_move_and_click(
                    round(off["left"] + rb_pos["x"]),
                    round(off["top"]  + rb_pos["y"]),
                )
            await page.wait_for_timeout(2000)
            ## Recropper les nouvelles images pour le prochain round
            captcha_info = await _setup_captcha_windows()
            return {"status": "refreshed", "captcha_waiting": captcha_info is not None,
                    **(captcha_info or {})}

        indices = [i for i in (req.indices or []) if isinstance(i, int)]
        if indices and bframe:
            positions = await bframe.evaluate(f"""() => {{
                const tiles = document.querySelectorAll(
                    'td.rc-imageselect-tile, .rc-imageselect-tile, [class*="imageselect-tile"]'
                );
                const idxs = {json.dumps(indices)};
                const pos  = [];
                for (const i of idxs) {{
                    if (tiles[i]) {{
                        const r = tiles[i].getBoundingClientRect();
                        pos.push({{i, x: r.left + r.width/2, y: r.top + r.height/2}});
                    }}
                }}
                return pos;
            }}""")
            off = await _get_bframe_offset()
            for pos in positions:
                await _human_move_and_click(
                    round(off["left"] + pos["x"]),
                    round(off["top"]  + pos["y"]),
                )
                await asyncio.sleep(random.uniform(0.3, 0.8))
            await page.wait_for_timeout(400)
            logging.info(f"{len(positions)} tile(s) cliquées")

        if bframe:
            try:
                btn_pos = await bframe.evaluate("""() => {
                    const btn = document.querySelector(
                        '#recaptcha-verify-button, .rc-button-default, [class*="verify"]'
                    );
                    if (!btn) return null;
                    const r = btn.getBoundingClientRect();
                    return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                }""")
                if btn_pos:
                    off = await _get_bframe_offset()
                    bx  = round(off["left"] + btn_pos["x"])
                    by  = round(off["top"]  + btn_pos["y"])
                    vh  = page.viewport_size["height"] if page.viewport_size else 900
                    if 0 < by < vh:
                        await _human_move_and_click(bx, by)
                    else:
                        await page.evaluate(f"window.scrollTo(0, {max(0, by - 400)})")
                        await page.wait_for_timeout(300)
                        await _human_move_and_click(bx, min(by, vh - 10))
                    logging.info(f"VERIFY ({bx}, {by})")
            except Exception as e:
                logging.warning(f"VERIFY échoué : {e}")

        await page.wait_for_timeout(3000)

        ## Vérifier si un nouveau round est en attente
        next_round = await _setup_captcha_windows()
        if next_round:
            return {"status": "captcha_waiting", **next_round}
        return {"status": "captcha_solved"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class ScreenshotReq(BaseModel):
    label: str = "manual"

@app.post("/screenshot")
async def take_screenshot(req: ScreenshotReq):
    try:
        return {"path": await _screenshot(req.label)}
    except Exception as e:
        raise HTTPException(500, str(e))


class CodeReq(BaseModel):
    code: str
    checkpoint_url: Optional[str] = None

@app.post("/code")
async def submit_code(req: CodeReq):
    """Soumet un code 2FA/OTP sur la page courante (ou navigue d'abord vers checkpoint_url)."""
    try:
        page = _B["page"]
        if req.checkpoint_url:
            await page.goto(req.checkpoint_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
        sels = [
            "input[id*='pin']", "input[name*='pin']",
            "input[autocomplete='one-time-code']", "input[type='text']:visible",
            "input[type='number']:visible",
        ]
        field = None
        for sel in sels:
            loc = page.locator(sel)
            if await loc.count() > 0:
                field = loc.first
                break
        if not field:
            shot = await _screenshot("code_notfound")
            return {"ok": False, "reason": "champ OTP introuvable", "screenshot": shot}
        await field.fill(req.code)
        await page.wait_for_timeout(500)
        submit_sels = ["button[type='submit']", "button:has-text('Vérifier')",
                       "button:has-text('Verify')", "button:has-text('Submit')",
                       "button:has-text('Envoyer')"]
        for s in submit_sels:
            btn = page.locator(s)
            if await btn.count() > 0:
                await btn.first.click()
                break
        else:
            await field.press("Enter")
        await page.wait_for_timeout(3000)
        shot = await _screenshot("code_submitted")
        info = await _detect_state()
        return {"ok": True, "state": info["state"], "url": page.url, "screenshot": shot}
    except Exception as e:
        raise HTTPException(500, str(e))


class ClickReq(BaseModel):
    x: float
    y: float

@app.post("/click")
async def click(req: ClickReq):
    try:
        await _human_move_and_click(req.x, req.y)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/shutdown")
async def shutdown():
    logging.info("Shutdown demandé")
    asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
    return {"ok": True}


class NavigateReq(BaseModel):
    url: str

@app.post("/navigate")
async def navigate(req: NavigateReq):
    """Navigue vers une URL arbitraire."""
    try:
        await _B["page"].goto(req.url, timeout=30000, wait_until="domcontentloaded")
        try:
            await _B["page"].wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        return {"ok": True, "url": _B["page"].url}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/captcha/click-checkbox")
async def captcha_click_checkbox():
    """Clique le checkbox reCAPTCHA 'I am not a robot' sur la page courante.

    Retourne captcha_waiting si un challenge image apparaît, captcha_solved sinon.
    """
    try:
        page = _B["page"]

        ## Attendre l'iframe anchor reCAPTCHA (contient le checkbox)
        anchor_frame = None
        for _ in range(8):
            await page.wait_for_timeout(1500)
            for frame in page.frames:
                if "recaptcha" in frame.url and "anchor" in frame.url:
                    anchor_frame = frame
                    break
            if anchor_frame:
                break

        if not anchor_frame:
            raise HTTPException(404, "reCAPTCHA anchor frame introuvable")

        ## Cliquer le checkbox dans l'iframe anchor
        clicked = False
        try:
            await anchor_frame.wait_for_load_state("domcontentloaded", timeout=8000)
            cb = await anchor_frame.evaluate("""() => {
                const sels = ['#recaptcha-anchor', '.recaptcha-checkbox',
                              '[role="checkbox"]', '.rc-anchor-checkbox'];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        return {sel: s, x: r.left + r.width/2, y: r.top + r.height/2};
                    }
                }
                return null;
            }""")
            if cb:
                await anchor_frame.click(cb["sel"], timeout=5000)
                logging.info(f"Checkbox cliqué (sel={cb['sel']})")
                clicked = True
            else:
                await anchor_frame.locator("body").click(position={"x": 24, "y": 37}, timeout=5000)
                logging.info("Checkbox cliqué (position fixe)")
                clicked = True
        except Exception as e:
            logging.warning(f"Clic anchor frame échoué ({e}) — fallback page.mouse")

        if not clicked:
            fr = await page.evaluate("""() => {
                for (const f of document.querySelectorAll('iframe')) {
                    if (f.src && f.src.includes('recaptcha') && f.src.includes('anchor')) {
                        const r = f.getBoundingClientRect();
                        return {left: r.left, top: r.top, w: f.offsetWidth, h: f.offsetHeight};
                    }
                }
                return null;
            }""")
            if fr and fr["w"] > 0:
                await _human_move_and_click(
                    fr["left"] + min(24, fr["w"] * 0.1),
                    fr["top"] + fr["h"] / 2,
                )
                logging.info("Checkbox cliqué (page.mouse fallback)")

        await page.wait_for_timeout(4000)

        captcha_info = await _setup_captcha_windows()
        if captcha_info:
            return {"status": "captcha_waiting", **captcha_info}
        return {"status": "captcha_solved"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    ap = argparse.ArgumentParser(description="Chrome Profile Server — daemon HTTP persistant")
    ap.add_argument("--apikey-id",      type=int, default=None,
                    help="ID de l'ApiKey en DB (requis si --demo-url absent)")
    ap.add_argument("--demo-url",       default=None,
                    help="Mode démo : naviguer directement à cette URL, sans DB ni GCS")
    ap.add_argument("--port",           type=int, default=8231)
    ap.add_argument("--screenshot-dir", default="/tmp")
    args = ap.parse_args()

    Path(args.screenshot_dir).mkdir(parents=True, exist_ok=True)

    if args.demo_url:
        _B.update(
            platform         = "demo",
            apikey_id        = None,
            gcs_profile_path = None,
            username         = None,
            password         = None,
            screenshot_dir   = args.screenshot_dir,
            demo_url         = args.demo_url,
        )
        Path("/tmp/cpm_server_demo.port").write_text(str(args.port))
        logging.info(f"Démarrage mode démo → {args.demo_url} (port {args.port})")
    else:
        if not args.apikey_id:
            print(json.dumps({"error": "--apikey-id requis si --demo-url absent"}))
            sys.exit(1)

        from gcpOpsci.db import get_db
        from gcpOpsci.models import ApiKey

        db  = next(get_db())
        ## Charger la clé QUEL QUE SOIT son statut : une reconnexion concerne le plus souvent une clé
        ## déjà `disactive` (profil déconnecté isolé par le triage). On ne réactive donc jamais la clé
        ## avant de reconnecter — le statut est rétabli à 'active' par l'appelant UNIQUEMENT après une
        ## reconnexion confirmée (logged_in). En cas d'échec, la clé reste simplement `disactive`.
        key = db.query(ApiKey).filter_by(id=args.apikey_id).first()
        db.close()

        if not key:
            print(json.dumps({"error": f"ApiKey #{args.apikey_id} introuvable"}))
            sys.exit(1)

        data    = key.json_data or {}
        profile = data.get("chrome_profile_path")
        if not profile:
            print(json.dumps({"error": "chrome_profile_path absent dans json_data"}))
            sys.exit(1)

        _B.update(
            platform         = key.platform,
            apikey_id        = args.apikey_id,
            gcs_profile_path = profile,
            username         = data.get("username"),
            password         = data.get("password"),
            screenshot_dir   = args.screenshot_dir,
        )
        Path(f"/tmp/cpm_server_{args.apikey_id}.port").write_text(str(args.port))
        logging.info(f"Démarrage — {key.platform} #{args.apikey_id} → port {args.port}")

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
