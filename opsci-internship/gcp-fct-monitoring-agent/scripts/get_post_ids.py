#!/usr/bin/env python3
"""Résout des pf_post_id en IDs de posts en base pour une importation donnée.

Usage:
    python get_post_ids.py <importation_id> <pf_post_id1> <pf_post_id2> ...
    echo '["pf1","pf2"]' | python get_post_ids.py <importation_id> -

Output: JSON array d'integers (post.id)

Exemple:
    python get_post_ids.py 697 "urn:li:activity:123" "urn:li:activity:456"
    python get_post_ids.py 697 - < /tmp/pf_ids.json
"""
import sys
import json
from gcpOpsci.db import get_db
from gcpOpsci.models import Post


def get_post_ids(importation_id: int, pf_post_ids: list[str]) -> list[int]:
    db = next(get_db())
    rows = db.query(Post.id, Post.pf_post_id).filter(
        Post.importation_id == importation_id,
        Post.pf_post_id.in_(pf_post_ids),
    ).all()
    db.close()

    found = {r.pf_post_id: r.id for r in rows}
    not_found = [p for p in pf_post_ids if p not in found]
    if not_found:
        print(f"[get_post_ids] ⚠️  {len(not_found)} pf_post_id(s) non trouvés en DB", file=sys.stderr)

    return [found[p] for p in pf_post_ids if p in found]


def main():
    if len(sys.argv) < 3:
        print("Usage: python get_post_ids.py <importation_id> <pf_post_id>... ou -", file=sys.stderr)
        sys.exit(1)

    importation_id = int(sys.argv[1])

    if sys.argv[2] == "-":
        pf_post_ids = json.load(sys.stdin)
    else:
        pf_post_ids = sys.argv[2:]

    ids = get_post_ids(importation_id, pf_post_ids)
    print(json.dumps(ids))


if __name__ == "__main__":
    main()
