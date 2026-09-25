#!/usr/bin/env python3
"""Retourne la révision active (latestReadyRevision) d'un service Cloud Run.

Usage:
    python get_revision.py <cf_url>

Output: nom court de la révision (ex: scrapper-linkedin-v2-00042-abc)

Variables d'environnement requises :
    GCP_PROJECT        — ID du projet GCP
    GCP_DB_ZONE        — Région Cloud Run (ex: europe-west1)
    GCP_PROJECT_NUMBER — Numéro du projet (optionnel, améliore la détection du nom de service)
"""
import sys
import json
import os
import re
import urllib.request


def get_revision(cf_url: str):
    token = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
            headers={'Metadata-Flavor': 'Google'}
        ), timeout=5
    ).read())['access_token']

    project = os.environ['GCP_PROJECT']
    region  = os.environ.get('GCP_DB_ZONE', 'europe-west1')
    pn      = os.environ.get('GCP_PROJECT_NUMBER', '')

    # Format URL Cloud Run : https://<service>-<project_number>.<region>.run.app
    if pn:
        m = re.match(rf'https://(.+)-{re.escape(pn)}\..+\.run\.app', cf_url)
    else:
        m = re.match(r'https://(.+?)-\d{10,}\..+\.run\.app', cf_url)

    if not m:
        print(f"Impossible d'extraire le nom du service depuis : {cf_url}", file=sys.stderr)
        sys.exit(1)

    service = m.group(1)
    url = f'https://run.googleapis.com/v2/projects/{project}/locations/{region}/services/{service}'

    data = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'}), timeout=10
    ).read())

    revision = data.get('latestReadyRevision', '').split('/')[-1]
    print(revision)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_revision.py <cf_url>", file=sys.stderr)
        sys.exit(1)
    get_revision(sys.argv[1])
