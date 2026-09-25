#!/usr/bin/env python3
"""Reprend une queue Cloud Tasks suspendue.

Usage:
    python resume_queue.py <queue_name>

Exemple:
    python resume_queue.py scrapper-linkedin-v2

Variables d'environnement requises :
    GCP_PROJECT — ID du projet GCP
    GCP_DB_ZONE — Région Cloud Tasks (ex: europe-west1)
"""
import sys
import json
import os
import urllib.request


def resume_queue(queue_name: str):
    if not queue_name:
        print('ℹ️  Pas de queue_name fourni — rien à reprendre', flush=True)
        return

    project = os.environ['GCP_PROJECT']
    region  = os.environ.get('GCP_DB_ZONE', 'europe-west1')

    token = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
            headers={'Metadata-Flavor': 'Google'}
        ), timeout=5
    ).read())['access_token']

    url = f'https://cloudtasks.googleapis.com/v2/projects/{project}/locations/{region}/queues/{queue_name}:resume'
    urllib.request.urlopen(
        urllib.request.Request(
            url, data=b'{}',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        ), timeout=10
    )
    print(f'✅ Queue {queue_name} reprise', flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python resume_queue.py <queue_name>", file=sys.stderr)
        sys.exit(1)
    resume_queue(sys.argv[1])
