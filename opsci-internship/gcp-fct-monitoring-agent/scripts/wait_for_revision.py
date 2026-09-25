#!/usr/bin/env python3
"""Attend qu'une nouvelle révision Cloud Run soit active pour un service donné.

Interroge l'API Cloud Run toutes les POLL_SECONDS secondes.
- Exit 0 : nouvelle révision prête et servant le trafic
- Exit 1 : timeout
- Exit 2 : nouvelle révision créée mais en erreur (container crash, config invalide)

Usage:
    python wait_for_revision.py <cf_url> <current_revision> [timeout_minutes=30] [poll_seconds=120]

Exemple:
    python wait_for_revision.py https://scrapper-linkedin-v2-000000000000.europe-west1.run.app rev-00019 30 120
"""
import sys
import json
import time
import urllib.request
import os
import re


def get_access_token() -> str:
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())["access_token"]


def get_service_name(cf_url: str) -> str:
    project_number = os.environ.get("GCP_PROJECT_NUMBER", "")
    if project_number:
        m = re.match(rf"https://(?P<service>.+)-{project_number}\..+\.run\.app", cf_url)
        if m:
            return m.group("service")
    raise ValueError(f"Impossible d'extraire le nom du service depuis : {cf_url}")


def get_service_status(token: str, project: str, region: str, service: str) -> dict:
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/services/{service}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return {
        "latest_ready":   data.get("latestReadyRevision",   "").split("/")[-1],
        "latest_created": data.get("latestCreatedRevision", "").split("/")[-1],
        "conditions":     data.get("conditions", []),
    }


def is_revision_failed(conditions: list) -> tuple[bool, str]:
    """Retourne (True, message) si la révision en cours de déploiement est en erreur."""
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("state") == "CONDITION_FAILED":
            msg = cond.get("message", "Révision en erreur")
            return True, msg
    return False, ""


def main():
    if len(sys.argv) < 3:
        print("Usage: python wait_for_revision.py <cf_url> <current_revision> [timeout_min] [poll_sec]",
              file=sys.stderr)
        sys.exit(1)

    cf_url           = sys.argv[1]
    current_revision = sys.argv[2]
    timeout_minutes  = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    poll_seconds     = int(sys.argv[4]) if len(sys.argv) > 4 else 120

    project = os.environ.get("GCP_PROJECT", "demo-project")
    region  = os.environ.get("GCP_DB_ZONE", "europe-west1")
    service = get_service_name(cf_url)
    deadline = time.time() + timeout_minutes * 60

    print(f"[wait_for_revision] Service={service} révision actuelle={current_revision} timeout={timeout_minutes}min", flush=True)

    while time.time() < deadline:
        try:
            token  = get_access_token()
            status = get_service_status(token, project, region, service)

            ready   = status["latest_ready"]
            created = status["latest_created"]

            # Nouvelle révision prête → succès
            if ready and ready != current_revision:
                print(f"[wait_for_revision] ✅ Nouvelle révision active : {ready}", flush=True)
                sys.exit(0)

            # Nouvelle révision créée mais en erreur → échec immédiat
            if created and created != current_revision:
                failed, msg = is_revision_failed(status["conditions"])
                if failed:
                    print(f"[wait_for_revision] ❌ Révision {created} en erreur : {msg}", flush=True)
                    sys.exit(2)
                print(f"[wait_for_revision] Révision {created} en cours de démarrage...", flush=True)
            else:
                remaining = int((deadline - time.time()) / 60)
                print(f"[wait_for_revision] Pas de nouvelle révision ({ready}), prochain check dans {poll_seconds}s (~{remaining}min restantes)", flush=True)

        except Exception as e:
            print(f"[wait_for_revision] ⚠️  Erreur API : {e}", flush=True)

        time.sleep(poll_seconds)

    print(f"[wait_for_revision] ❌ Timeout après {timeout_minutes} minutes", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
