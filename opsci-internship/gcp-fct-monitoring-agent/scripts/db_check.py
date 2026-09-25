#!/usr/bin/env python3
"""Diagnostic DB complet — vérifie les champs et relations populés par le pipeline scraper → json-parser.

Tables vérifiées : posts, accounts, documents, tags, join_post_account, join_post_document,
                   join_post_post, join_post_tag.

Usage:
    python db_check.py <importation_id> [limit=20]
    python db_check.py --post-ids 1,2,3
    python db_check.py --post-ids '[1,2,3]'   # JSON array

Retourne un JSON avec les alertes détectées (critiques + informatives) et un échantillon de posts.
"""
import sys
import json
from gcpOpsci.db import get_db
from sqlalchemy import text


def _build_placeholders(ids: list[int]) -> tuple[str, dict]:
    placeholders = ",".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": pid for i, pid in enumerate(ids)}
    return placeholders, params


def _fetch_rows(db, where_clause: str, params: dict, limit: int) -> list[dict]:
    """Requête principale : champs du post + counts de toutes les relations.

    Le tri + LIMIT sur `posts` est résolu D'ABORD, dans une CTE matérialisée qui n'applique QUE
    `where_clause` (2026-07-28) — sans ça, Postgres peut choisir de trier TOUTE la table `posts`
    via l'index sur `created_at` et de filtrer `where_clause` après coup ligne par ligne, au lieu
    de filtrer d'abord par `importation_id`/`id` (sélectif). Piège confirmé en pratique : sur une
    importation ancienne/peu peuplée relativement au tri global, ce plan scanne des millions de
    lignes avant de trouver `limit` correspondances (timeout observé, cf. le même risque déjà
    documenté et contourné par LATERAL JOIN dans gcp-fct-coordination/utils/coordination.py). Les
    jointures/agrégations ne portent plus que sur les `limit` lignes déjà sélectionnées, pas sur
    tout le scope.
    """
    rows = db.execute(text(f"""
        WITH scoped_posts AS MATERIALIZED (
            SELECT
                p.id, p.pf_post_id, p.platform, p.type, p.url, p.is_deduced, p.created_at,
                p.reactions, p.comments, p.shares, p.engagements, p.last_parsing_date
            FROM posts p
            WHERE {where_clause}
            ORDER BY p.created_at DESC NULLS LAST
            LIMIT :limit
        )
        SELECT
            sp.id,
            sp.pf_post_id,
            sp.platform,
            sp.type,
            sp.url,
            sp.is_deduced,
            sp.created_at,
            sp.reactions,
            sp.comments,
            sp.shares,
            sp.engagements,
            sp.last_parsing_date,
            COUNT(DISTINCT jpd.to_document_id)  AS nb_documents,
            COUNT(DISTINCT jpa.to_account_id)   AS nb_accounts,
            COUNT(DISTINCT jpp.to_post_id)      AS nb_related_posts,
            COUNT(DISTINCT jpt.tag_id)          AS nb_tags
        FROM scoped_posts sp
        LEFT JOIN join_post_document jpd ON jpd.from_post_id = sp.id
        LEFT JOIN join_post_account  jpa ON jpa.from_post_id = sp.id
        LEFT JOIN join_post_post     jpp ON jpp.from_post_id = sp.id
        LEFT JOIN join_post_tag      jpt ON jpt.post_id      = sp.id
        GROUP BY sp.id, sp.pf_post_id, sp.platform, sp.type, sp.url, sp.is_deduced, sp.created_at,
                 sp.reactions, sp.comments, sp.shares, sp.engagements, sp.last_parsing_date
        ORDER BY sp.created_at DESC NULLS LAST
    """), {**params, "limit": limit}).mappings().fetchall()
    return [dict(r) for r in rows]


def _fetch_account_quality(db, placeholders: str, id_params: dict) -> dict:
    """Vérifie la qualité des accounts liés : screen_name et pf_account_id manquants."""
    result = db.execute(text(f"""
        SELECT
            COUNT(DISTINCT CASE WHEN a.screen_name IS NULL THEN jpa.to_account_id END)  AS missing_screen_name,
            COUNT(DISTINCT CASE WHEN a.pf_account_id IS NULL THEN jpa.to_account_id END) AS missing_pf_account_id
        FROM join_post_account jpa
        JOIN accounts a ON a.id = jpa.to_account_id
        WHERE jpa.from_post_id IN ({placeholders})
          AND jpa.type = 'author'
    """), id_params).mappings().fetchone()
    return dict(result) if result else {}


def _fetch_document_quality(db, placeholders: str, id_params: dict) -> dict:
    """Vérifie la qualité des documents : documents texte sans contenu, url/image/video sans url."""
    result = db.execute(text(f"""
        SELECT
            COUNT(DISTINCT CASE WHEN d.type = 'text' AND (d.text IS NULL OR d.text = '') THEN d.id END) AS text_docs_empty,
            COUNT(DISTINCT CASE WHEN d.type IN ('url','image','video') AND d.url IS NULL THEN d.id END)  AS media_docs_no_url
        FROM join_post_document jpd
        JOIN documents d ON d.id = jpd.to_document_id
        WHERE jpd.from_post_id IN ({placeholders})
    """), id_params).mappings().fetchone()
    return dict(result) if result else {}


def _build_alerts(sample: list[dict], unparsed: int, account_quality: dict, doc_quality: dict) -> dict:
    critiques = {}
    informatifs = {}

    if sample:
        # --- Critiques : bloquent le pipeline ou signalent un Level 0
        critiques["unparsed"]          = unparsed
        critiques["null_pf_post_id"]   = sum(1 for r in sample if r["pf_post_id"] is None)
        critiques["null_created_at"]   = sum(1 for r in sample if r["created_at"] is None)
        critiques["null_platform"]     = sum(1 for r in sample if r["platform"] is None)
        critiques["null_type"]         = sum(1 for r in sample if r["type"] is None)
        critiques["zero_accounts"]     = sum(1 for r in sample if r["nb_accounts"] == 0)
        critiques["authors_missing_screen_name"]   = account_quality.get("missing_screen_name", 0)
        critiques["authors_missing_pf_account_id"] = account_quality.get("missing_pf_account_id", 0)

        # --- Informatifs : peuvent être légitimes selon la plateforme/le type de post
        informatifs["null_url"]             = sum(1 for r in sample if r["url"] is None)
        informatifs["zero_documents"]       = sum(1 for r in sample if r["nb_documents"] == 0)
        informatifs["zero_engagements"]     = sum(1 for r in sample if (r["reactions"] or 0) == 0 and (r["comments"] or 0) == 0 and (r["shares"] or 0) == 0)
        informatifs["zero_related_posts"]   = sum(1 for r in sample if r["nb_related_posts"] == 0)
        informatifs["zero_tags"]            = sum(1 for r in sample if r["nb_tags"] == 0)
        informatifs["text_docs_empty"]      = doc_quality.get("text_docs_empty", 0)
        informatifs["media_docs_no_url"]    = doc_quality.get("media_docs_no_url", 0)

    return {"critiques": critiques, "informatifs": informatifs}


def _print_result(importation_id, total, unparsed, sample, account_quality, doc_quality, post_ids=None):
    alerts = _build_alerts(sample, unparsed, account_quality, doc_quality)

    result = {
        "total_posts": total,
        "unparsed_posts": unparsed,
        "sample_size": len(sample),
        "alerts": alerts,
        "sample": sample[:5],
    }
    if importation_id is not None:
        result["importation_id"] = importation_id

    print(json.dumps(result, indent=2, default=str))


def check_importation(importation_id: int, limit: int = 20):
    db = next(get_db())
    try:
        total = db.execute(
            text("SELECT COUNT(*) FROM posts WHERE importation_id = :iid"),
            {"iid": importation_id}
        ).scalar() or 0

        unparsed = db.execute(
            text("SELECT COUNT(*) FROM posts WHERE importation_id = :iid AND last_parsing_date IS NULL"),
            {"iid": importation_id}
        ).scalar() or 0

        sample = _fetch_rows(db, "p.importation_id = :iid", {"iid": importation_id}, limit)

        # Pour les checks de qualité, on récupère les IDs de l'échantillon
        sample_ids = [r["id"] for r in sample]
        account_quality, doc_quality = {}, {}
        if sample_ids:
            placeholders, id_params = _build_placeholders(sample_ids)
            account_quality = _fetch_account_quality(db, placeholders, id_params)
            doc_quality     = _fetch_document_quality(db, placeholders, id_params)

        _print_result(importation_id, total, unparsed, sample, account_quality, doc_quality)

    except Exception as e:
        print(json.dumps({"error": str(e), "importation_id": importation_id}), file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def check_post_ids(post_ids: list[int], limit: int = 200):
    db = next(get_db())
    try:
        if not post_ids:
            print(json.dumps({"error": "post_ids est vide"}), file=sys.stderr)
            sys.exit(1)

        placeholders, id_params = _build_placeholders(post_ids)

        total = len(post_ids)
        unparsed = db.execute(
            text(f"SELECT COUNT(*) FROM posts WHERE id IN ({placeholders}) AND last_parsing_date IS NULL"),
            id_params
        ).scalar() or 0

        sample   = _fetch_rows(db, f"p.id IN ({placeholders})", id_params, min(limit, total))
        account_quality = _fetch_account_quality(db, placeholders, id_params)
        doc_quality     = _fetch_document_quality(db, placeholders, id_params)

        _print_result(None, total, unparsed, sample, account_quality, doc_quality, post_ids=post_ids)

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print("Usage:", file=sys.stderr)
        print("  python db_check.py <importation_id> [limit]", file=sys.stderr)
        print("  python db_check.py --post-ids 1,2,3", file=sys.stderr)
        print("  python db_check.py --post-ids '[1,2,3]'", file=sys.stderr)
        sys.exit(1)

    if args[0] == "--post-ids":
        if len(args) < 2:
            print("--post-ids requiert une valeur", file=sys.stderr)
            sys.exit(1)
        raw = args[1]
        if raw.startswith("["):
            post_ids = json.loads(raw)
        else:
            post_ids = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
        check_post_ids(post_ids)
    else:
        check_importation(int(args[0]), int(args[1]) if len(args) > 1 else 20)
