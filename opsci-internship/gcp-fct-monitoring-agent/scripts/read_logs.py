#!/usr/bin/env python3
"""Lecture des logs Cloud Logging pour une CF ou un service Cloud Run.

Dérive automatiquement le filtre depuis l'URL de la CF grâce à GCP_PROJECT_NUMBER.

Usage:
    python read_logs.py <cf_url> [limit=30] [freshness=1h] [severity=]

Exemples:
    python read_logs.py https://scrapper-linkedin-v2-000000000000.europe-west1.run.app
    python read_logs.py https://europe-west1-demo-project.cloudfunctions.net/scrapper-facebook 50 2h
    python read_logs.py https://monitoring-agent-000000000000.europe-west1.run.app 100 0.5 DEBUG
"""
import sys
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone


def get_access_token() -> str:
    """ Récupère le token d'accès via le metadata server Cloud Run """
    url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())["access_token"]


def build_log_filter(cf_url: str) -> str:
    """ Construit le filtre Cloud Logging depuis l'URL de la CF """
    project_number = os.environ.get("GCP_PROJECT_NUMBER", "")

    # Cloud Run v2 — avec GCP_PROJECT_NUMBER explicite
    if project_number:
        m = re.match(rf"https://(.+)-{re.escape(str(project_number))}\..+\.run\.app", cf_url)
        if m:
            return f'resource.type="cloud_run_revision" AND resource.labels.service_name="{m.group(1)}"'

    # Cloud Run v2 — fallback : le project number est un bloc de chiffres dans l'URL
    m = re.match(r"https://(.+?)-\d{8,}\..+\.run\.app", cf_url)
    if m:
        return f'resource.type="cloud_run_revision" AND resource.labels.service_name="{m.group(1)}"'

    # Cloud Functions (gen1/gen2 via cloudfunctions.net)
    m = re.match(r"https://.+\.cloudfunctions\.net/(.+)", cf_url)
    if m:
        return f'resource.type="cloud_function" AND resource.labels.function_name="{m.group(1)}"'

    raise ValueError(f"URL non reconnue (Cloud Run ou Cloud Function attendu) : {cf_url}")


def read_logs(cf_url: str, limit: int = 30, freshness_hours: float = 1.0, severity: str = ""):
    """ Lit et affiche les logs Cloud Logging pour la CF donnée """
    project = os.environ.get("GCP_PROJECT", "demo-project")
    log_filter = build_log_filter(cf_url)

    since = (datetime.now(timezone.utc) - timedelta(hours=freshness_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_filter += f' AND timestamp>="{since}"'
    if severity:
        log_filter += f' AND severity="{severity.upper()}"'

    token = get_access_token()
    payload = json.dumps({
        "resourceNames": [f"projects/{project}"],
        "filter": log_filter,
        "orderBy": "timestamp desc",
        "pageSize": limit,
    }).encode()

    req = urllib.request.Request(
        "https://logging.googleapis.com/v2/entries:list",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    result = json.loads(urllib.request.urlopen(req, timeout=15).read())
    entries = result.get("entries", [])

    if not entries:
        print(f"[read_logs] Aucun log trouvé (filtre: {log_filter})")
        return

    for entry in reversed(entries):
        ts = entry.get("timestamp", "")[:19]
        text = entry.get("textPayload") or json.dumps(entry.get("jsonPayload", ""))
        severity = entry.get("severity", "")
        print(f"{ts} [{severity}] {text}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python read_logs.py <cf_url> [limit] [freshness_hours]", file=sys.stderr)
        sys.exit(1)
    read_logs(
        cf_url=sys.argv[1],
        limit=int(sys.argv[2]) if len(sys.argv) > 2 else 30,
        freshness_hours=float(sys.argv[3].replace("h", "")) if len(sys.argv) > 3 else 1.0,
        severity=sys.argv[4] if len(sys.argv) > 4 else "",
    )
