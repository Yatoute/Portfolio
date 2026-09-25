#!/usr/bin/env python3
"""Utilitaires DB pour scrapers opaques (sans just_scrape=True).

Usage:
    # Mode 1 — max Post.id actuel pour une importation (à noter AVANT la collecte)
    python get_new_post_ids.py --max-id <importation_id>

    # Mode 2 — IDs des posts insérés après la collecte
    python get_new_post_ids.py --since <max_id_before> <importation_id>

Exemple:
    MAX_ID=$(python get_new_post_ids.py --max-id 697)
    # ... collecte ...
    POST_IDS=$(python get_new_post_ids.py --since $MAX_ID 697)
"""
import sys
import json
import sqlalchemy
from gcpOpsci.db import get_db
from gcpOpsci.models import Post


def get_max_id(importation_id: int):
    db = next(get_db())
    try:
        max_id = db.query(sqlalchemy.func.max(Post.id)).filter(
            Post.importation_id == importation_id
        ).scalar() or 0
        print(max_id)
    finally:
        db.close()


def get_new_ids(importation_id: int, since_id: int):
    db = next(get_db())
    try:
        posts = db.query(Post.id).filter(
            Post.importation_id == importation_id,
            Post.id > since_id
        ).all()
        print(json.dumps([r[0] for r in posts]))
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage:", file=sys.stderr)
        print("  python get_new_post_ids.py --max-id <importation_id>", file=sys.stderr)
        print("  python get_new_post_ids.py --since <max_id_before> <importation_id>", file=sys.stderr)
        sys.exit(1)

    if args[0] == "--max-id":
        get_max_id(int(args[1]))
    elif args[0] == "--since":
        if len(args) < 3:
            print("--since requiert <max_id_before> <importation_id>", file=sys.stderr)
            sys.exit(1)
        get_new_ids(int(args[2]), int(args[1]))
    else:
        print(f"Argument inconnu : {args[0]}", file=sys.stderr)
        sys.exit(1)
