#!/usr/bin/env python3
"""Interception Playwright pour le monitoring Level 2 — paramètres de requête changés.

Navigue vers une URL avec un profil Chrome persistant, intercepte les requêtes
correspondant à un mot-clé, et sauvegarde chaque paire requête/réponse dans output_dir.

Améliorations vs version précédente :
  - Toutes les requêtes sont indexées par friendly_name + doc_id (pas seulement filtrées)
  - Manifest JSON global avec l'index de toutes les requêtes capturées
  - Option --friendly-name pour capturer UNE requête spécifique par nom
  - go_back / go_forward optionnel pour déclencher les SPA prefetch queries
  - Scroll automatique pour déclencher le lazy-loading

Usage:
    python playwright_intercept.py \
        --profile <chrome_profile_dir> \
        --url <navigate_url> \
        --keyword <filter_keyword> \
        --output <output_dir> \
        [--max <max_requests=5>] \
        [--friendly-name <ProfileCometHeaderQuery>] \
        [--spa-back] \
        [--headless]

Exemple Facebook profile :
    python playwright_intercept.py \
        --profile /chrome-profile/chrome-profiles/account_1 \
        --url "https://www.facebook.com/catherine.kombate.1" \
        --keyword graphql \
        --output /tmp/intercept \
        --spa-back \
        --max 20
"""
import sys
import json
import argparse
import asyncio
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright


async def intercept(
    profile_dir: str,
    navigate_url: str,
    keyword: str,
    output_dir: str,
    max_requests: int = 5,
    headless: bool = True,
    friendly_name_filter: str = None,
    spa_back: bool = False,
):
    """
    Paramètres
    ----------
    friendly_name_filter : si fourni, ne sauvegarde QUE la requête dont
        le friendly_name (résolu depuis x-fb-friendly-name header, body POST,
        queryId URL ou path URL selon la plateforme) correspond exactement.
    spa_back : si True, charge d'abord l'URL principale puis fait home→URL
        pour déclencher les SPA prefetch queries (ex: ProfileCometHeaderQuery).
        Utile pour Facebook où certaines requêtes ne se déclenchent qu'en
        navigation SPA (clic React Router), pas en goto() direct.
    """
    captured      = []   # fichiers sauvegardés
    all_seen      = {}   # index friendly_name → {doc_id, size} — même sans sauvegarde
    output_path   = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async def handle_response(response):
        if keyword not in response.url:
            return
        try:
            req          = response.request
            post_data    = req.post_data or ""
            headers      = dict(req.headers)

            parsed_qs   = urllib.parse.parse_qs(post_data)
            url_qs      = urllib.parse.parse_qs(urllib.parse.urlparse(response.url).query)

            ## Friendly name — ordre de priorité multi-plateformes :
            ## 1. Facebook : x-fb-friendly-name header
            ## 2. Facebook : fb_api_req_friendly_name dans le body POST
            ## 3. LinkedIn / autres : queryId dans l'URL (ex: voyagerSearchDashClusters.b0928897...)
            ## 4. Fallback : dernier segment du path URL
            def _friendly_from_url(url: str) -> str:
                path = urllib.parse.urlparse(url).path.rstrip("/")
                return path.split("/")[-1] or "unknown"

            friendly = (
                headers.get("x-fb-friendly-name")
                or parsed_qs.get("fb_api_req_friendly_name", [None])[0]
                or (url_qs.get("queryId", [None])[0] or "").split(".")[0] or None
                or _friendly_from_url(response.url)
            )

            ## doc_id (Facebook) ou queryId complet (LinkedIn/autres)
            doc_id = (
                parsed_qs.get("doc_id", [None])[0]
                or url_qs.get("queryId", [None])[0]
                or next((v[0] for k, v in url_qs.items() if "doc_id" in k.lower()), None)
            )

            try:
                body = await response.json()
                size = len(json.dumps(body))
            except Exception:
                body = await response.text()
                size = len(body)

            ## Indexer toutes les requêtes vues (même sans les sauvegarder)
            all_seen[friendly] = {
                "doc_id":     doc_id,
                "size":       size,
                "url":        req.url,
            }

            ## Décider si on sauvegarde
            should_save = (
                len(captured) < max_requests
                and (
                    friendly_name_filter is None
                    or friendly == friendly_name_filter
                )
            )
            if not should_save:
                return

            entry = {
                "friendly_name": friendly,
                "doc_id":        doc_id,
                "request": {
                    "url":       req.url,
                    "method":    req.method,
                    "headers":   headers,
                    "post_data": post_data,
                },
                "response": {
                    "status": response.status,
                    "body":   body,
                },
            }

            safe = friendly.replace("/", "_")[:60]
            fname = output_path / f"{len(captured):03d}_{safe}.json"
            fname.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
            print(f"[intercept] Capturé ({len(captured)+1}/{max_requests}): {friendly} (doc_id={doc_id})")
            captured.append({"file": str(fname), "friendly_name": friendly, "doc_id": doc_id})

        except Exception as e:
            print(f"[intercept] Erreur: {e}", file=sys.stderr)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--password-store=basic",
            ],
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_default_args=["--enable-automation"],
        )
        page = await context.new_page()
        page.on("response", handle_response)

        if spa_back:
            ## Charger l'URL cible d'abord pour peupler l'historique
            print(f"[intercept] [spa_back] Navigation initiale → {navigate_url}")
            await page.goto(navigate_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            ## Retourner sur la home (ou URL parente)
            base = "/".join(navigate_url.split("/")[:3])  # ex: https://www.facebook.com
            print(f"[intercept] [spa_back] Retour sur {base}")
            await page.goto(base, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            ## go_back vers l'URL cible — déclenche les SPA prefetch queries
            print(f"[intercept] [spa_back] go_back() → devrait déclencher les SPA queries")
            await page.go_back(wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)
        else:
            print(f"[intercept] Navigation vers {navigate_url}")
            await page.goto(navigate_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(8000)

        ## Scroll pour déclencher le lazy-loading
        if len(captured) < max_requests:
            await page.evaluate("window.scrollBy(0, 1000)")
            await page.wait_for_timeout(3000)
            await page.evaluate("window.scrollBy(0, 1000)")
            await page.wait_for_timeout(2000)

        await context.close()

    ## Sauvegarder le manifest avec TOUTES les requêtes vues
    manifest = {
        "captured_count":   len(captured),
        "output_dir":       str(output_path.resolve()),
        "files":            captured,
        ## Index de toutes les requêtes GraphQL vues (même non sauvegardées)
        ## Utile pour l'agent de monitoring : il peut voir tous les doc_ids disponibles
        "all_requests_seen": [
            {"friendly_name": k, **v}
            for k, v in sorted(all_seen.items(), key=lambda x: -x[1]["size"])
        ],
    }
    manifest_path = output_path / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(json.dumps(manifest, indent=2))
    return captured


def main():
    parser = argparse.ArgumentParser(description="Playwright request interceptor")
    parser.add_argument("--profile",       required=True)
    parser.add_argument("--url",           required=True)
    parser.add_argument("--keyword",       required=True)
    parser.add_argument("--output",        required=True)
    parser.add_argument("--max",           type=int, default=5)
    parser.add_argument("--friendly-name", default=None,
                        help="Filtrer sur UNE friendly_name spécifique (ex: ProfileCometHeaderQuery)")
    parser.add_argument("--spa-back",      action="store_true", default=False,
                        help="Charger URL → home → go_back pour déclencher SPA prefetch queries")
    parser.add_argument("--headless",      action="store_true", default=True)
    args = parser.parse_args()

    asyncio.run(intercept(
        profile_dir=args.profile,
        navigate_url=args.url,
        keyword=args.keyword,
        output_dir=args.output,
        max_requests=args.max,
        headless=args.headless,
        friendly_name_filter=args.friendly_name,
        spa_back=args.spa_back,
    ))


if __name__ == "__main__":
    main()
