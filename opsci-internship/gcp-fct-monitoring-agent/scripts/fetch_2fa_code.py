#!/usr/bin/env python3
"""Récupère le code 2FA email d'un compte depuis sa boîte Gmail (IMAP read-only).

Lit l'adresse (`ApiKey.json_data['username']`) et le mot de passe d'application
(`ApiKey.json_data['imap_app_password']`), se connecte en IMAP read-only à Gmail,
filtre les mails de vérification récents (Instagram / Facebook), ignore les notifications
("New login…"), et imprime le code à 6 chiffres du mail **le plus récent** dans la fenêtre
de fraîcheur.

Sert au flux de reconnexion automatique :
  - 2FA EMAIL  → un code récent existe → ce script l'imprime → l'orchestrateur le soumet via POST /code.
  - 2FA SMS / approbation appareil → AUCUN code email récent → ce script ne renvoie rien (exit 1)
    → rester en intervention manuelle.

Le filtre de fraîcheur (`--max-age`, défaut 180 s) garantit qu'on n'utilise PAS un vieux code
d'une tentative précédente : seul un code déclenché par la tentative de login en cours est pris.

Usage:
    CODE=$(python fetch_2fa_code.py <apikey_id> [--max-age 180] [--scan-last 15] [--wait 60])
    # → "503846"  (stdout, exit 0)   |   rien (exit 1) si aucun code récent

Prérequis : `ApiKey.json_data` doit contenir `username` (adresse Gmail) et `imap_app_password`
(cf. notebooks/dev_scrapper/2fa_email_imap_setup.md).
"""
import sys
import re
import time
import imaplib
import email
import argparse
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

from gcpOpsci.db import get_db
from gcpOpsci.models import ApiKey

## Expéditeurs des mails de code (sous-chaîne, insensible à la casse)
_VERIF_SENDERS = ("instagram.com", "facebookmail.com", "facebook.com")
## Notifications à IGNORER (contiennent un nombre à 6 chiffres mais PAS un code de vérif)
_IGNORE_SUBJECT = ("new login", "nouvelle connexion", "added", "ajouté",
                   "recently added", "ont récemment", "log in", "se connecter")


def _decode(value: str) -> str:
    if not value:
        return ""
    return "".join(
        (b.decode(enc or "utf-8", "ignore") if isinstance(b, bytes) else b)
        for b, enc in decode_header(value)
    )


def _body_text(msg) -> str:
    """Concatène les parties text/plain et text/html (HTML brut, le regex s'en accommode)."""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    chunks.append(part.get_payload(decode=True).decode("utf-8", "ignore"))
                except Exception:
                    pass
    else:
        try:
            chunks.append(msg.get_payload(decode=True).decode("utf-8", "ignore"))
        except Exception:
            pass
    return "\n".join(chunks)


def _extract_code(text: str):
    """Code à 6 chiffres ; on privilégie celui proche du mot 'code'/'vérification'."""
    if not text:
        return None
    for pat in (r'code[^0-9]{0,20}(\d{6})', r'(\d{6})[^0-9]{0,25}code',
                r'v[ée]rification[^0-9]{0,20}(\d{6})'):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    m = re.search(r'\b(\d{6})\b', text)
    return m.group(1) if m else None


def _creds(apikey_id: int):
    db = next(get_db())
    try:
        key = db.query(ApiKey).filter(ApiKey.id == apikey_id).first()
        if not key:
            raise SystemExit(f"ApiKey #{apikey_id} introuvable")
        jd = key.json_data or {}
        emailaddr = jd.get("username")
        app_pw = jd.get("imap_app_password")
        if not emailaddr or not app_pw:
            raise SystemExit(
                f"ApiKey #{apikey_id} : 'username' ou 'imap_app_password' manquant dans json_data "
                "(cf. 2fa_email_imap_setup.md)"
            )
        return emailaddr, app_pw
    finally:
        db.close()


def fetch_code(apikey_id: int, max_age_seconds: int = 180, scan_last: int = 15):
    """Retourne (code, mail_date) du mail de vérification le plus récent dans la fenêtre, sinon (None, None)."""
    emailaddr, app_pw = _creds(apikey_id)
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        M.login(emailaddr, app_pw)
        M.select("inbox", readonly=True)
        typ, data = M.search(None, "ALL")
        ids = data[0].split()[-scan_last:][::-1]  # N plus récents, du + récent au + ancien
        now = datetime.now(timezone.utc)
        best = None  # (date, code)
        for num in ids:
            typ, md = M.fetch(num, "(RFC822)")
            if not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            frm = _decode(msg.get("From")).lower()
            subj = _decode(msg.get("Subject"))
            if not any(s in frm for s in _VERIF_SENDERS):
                continue
            if any(ig in subj.lower() for ig in _IGNORE_SUBJECT):
                continue
            code = _extract_code(subj) or _extract_code(_body_text(msg))
            if not code:
                continue
            try:
                mdate = parsedate_to_datetime(msg.get("Date"))
                if mdate and mdate.tzinfo is None:
                    mdate = mdate.replace(tzinfo=timezone.utc)
            except Exception:
                mdate = None
            ## fraîcheur : écarte les vieux codes (tentative précédente / 2FA non-email)
            if max_age_seconds and mdate and (now - mdate).total_seconds() > max_age_seconds:
                continue
            ## garder le plus récent
            if best is None or (mdate and (best[0] is None or mdate > best[0])):
                best = (mdate, code)
        return (best[1], best[0]) if best else (None, None)
    finally:
        try:
            M.logout()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="Récupère le code 2FA email d'un ApiKey via IMAP.")
    ap.add_argument("apikey_id", type=int)
    ap.add_argument("--max-age", type=int, default=180,
                    help="Fenêtre de fraîcheur en secondes (défaut 180 ; 0 = ignorer)")
    ap.add_argument("--scan-last", type=int, default=15,
                    help="Nombre de mails récents à scanner (défaut 15)")
    ap.add_argument("--wait", type=int, default=0,
                    help="Si >0 : poll toutes les 5 s jusqu'à trouver un code ou atteindre ce délai (s)")
    args = ap.parse_args()

    deadline = time.time() + args.wait
    while True:
        code, mdate = fetch_code(args.apikey_id, max_age_seconds=args.max_age, scan_last=args.scan_last)
        if code:
            print(code)
            sys.stderr.write(f"✅ code trouvé (mail du {mdate})\n")
            return 0
        if args.wait <= 0 or time.time() >= deadline:
            sys.stderr.write("⚠️  aucun code email récent (2FA non-email ? → reconnexion manuelle)\n")
            return 1
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
