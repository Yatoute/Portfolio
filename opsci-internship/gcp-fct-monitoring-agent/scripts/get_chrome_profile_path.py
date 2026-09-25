#!/usr/bin/env python3
"""Retourne la liste ordonnée des profils Chrome GCS pour une plateforme.

Stratégie :
  1. Tous les ApiKeys actifs de la plateforme ayant un chrome_profile_path,
     triés par last_used_at DESC (le plus récemment utilisé = le plus susceptible d'avoir une session valide)
  2. Profil de l'agent ($MONITORING_AGENT_CHROME_PROFILE) en dernier recours

Output: JSON array de chemins GCS relatifs (ex: ["chrome-profiles/account_2", "chrome-profiles/account_1"])
        Les chemins sont à préfixer par /chrome-profile/ pour accéder au bucket monté.

Usage:
    python get_chrome_profile_path.py <platform>

Exemple:
    CHROME_PROFILES=$(python get_chrome_profile_path.py Facebook)
    # → ["chrome-profiles/account_2", "chrome-profiles/account_1", "chrome-profiles/monitoring_agent"]
    for profile in $(echo "$CHROME_PROFILES" | python3 -c "import json,sys; [print(p) for p in json.load(sys.stdin)]"); do
      ls "/chrome-profile/$profile" && SELECTED="$profile" && break
    done
"""
import sys
import json
import os
from gcpOpsci.db import get_db
from gcpOpsci.models import ApiKey


def get_profile_paths(platform: str) -> list[str]:
    db = next(get_db())
    paths = []
    try:
        keys = (
            db.query(ApiKey)
            .filter(ApiKey.platform == platform, ApiKey.status == 'active')
            .order_by(ApiKey.last_used_at.desc().nulls_last())
            .all()
        )
        for key in keys:
            if not key.json_data:
                continue
            path = key.json_data.get('chrome_profile_path', '')
            if path and path not in paths:
                paths.append(path)
    finally:
        db.close()

    # Profil de l'agent en dernier recours
    agent_profile = os.environ.get('MONITORING_AGENT_CHROME_PROFILE', '')
    if agent_profile and agent_profile not in paths:
        paths.append(agent_profile)

    return paths


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_chrome_profile_path.py <platform>", file=sys.stderr)
        sys.exit(1)

    paths = get_profile_paths(sys.argv[1])
    print(json.dumps(paths))

    if not paths:
        sys.exit(1)
