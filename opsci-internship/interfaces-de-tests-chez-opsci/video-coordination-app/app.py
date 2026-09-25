"""
Application de visualisation des reprises de séquences vidéo.

Chargement depuis la BDD : reconstruction depuis join_chunk_chunk (type='coordination').
Les métadonnées de scènes (transcripts, start_ms, keyframes) sont enrichies depuis la BDD.
Les images keyframes sont téléchargées depuis GCS.

Endpoints :
  GET /                          → formulaire
  GET /reprises?analyse_id=509   → chargement BDD
  GET /media/<work_key>/<path>   → images keyframes locales

Lancement :
  cd notebooks/video-mining/video-coordination-app
  python app.py
"""

import hashlib
import os
import shutil
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, render_template, request, send_from_directory
from google.cloud import storage
from sqlalchemy import text

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

## Injecter les vars BDD avant le premier appel à get_settings() (cache lru_cache)
from gcpOpsci.settings import get_settings
get_settings.cache_clear()

from gcpOpsci.db import get_db


WORK_DIR = "/tmp/reprises_app"
WORK_DIR_MAX_AGE = 3600

os.makedirs(WORK_DIR, exist_ok=True)

app = Flask(__name__)
_storage_client = None


def get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


# =============================================================================
# HELPERS BDD
# =============================================================================

def fetch_scene_metadata(scene_doc_ids: list[int]) -> dict[int, dict]:
    """
    Retourne {scene_doc_id: {transcript, scene_index, start_ms, end_ms, start_frame, end_frame}}
    depuis les documents de type 'scene'.
    """
    if not scene_doc_ids:
        return {}
    db = next(get_db())
    try:
        rows = db.execute(text("""
            SELECT
                id,
                text                                              AS transcript,
                COALESCE((json_data->>'scene_index')::int,  0)   AS scene_index,
                COALESCE((json_data->>'start_ms')::int,     0)   AS start_ms,
                COALESCE((json_data->>'end_ms')::int,       0)   AS end_ms,
                COALESCE((json_data->>'start_frame')::int,  0)   AS start_frame,
                COALESCE((json_data->>'end_frame')::int,    0)   AS end_frame
            FROM documents
            WHERE id = ANY(:ids)
              AND type = 'scene'
        """), {"ids": scene_doc_ids}).fetchall()

        return {
            row.id: {
                "transcript":  row.transcript or "",
                "scene_index": row.scene_index,
                "start_ms":    row.start_ms,
                "end_ms":      row.end_ms,
                "start_frame": row.start_frame,
                "end_frame":   row.end_frame,
            }
            for row in rows
        }
    finally:
        db.close()


def fetch_scene_doc_ids_by_chunk_ids(chunk_ids: list[int]) -> dict[int, int]:
    """Retourne {chunk_id: scene_doc_id} via analyses_chunks → join_document_document → documents."""
    if not chunk_ids:
        return {}
    db = next(get_db())
    try:
        rows = db.execute(text("""
            SELECT ac.id AS chunk_id, d.id AS scene_doc_id
            FROM analyses_chunks ac
            JOIN join_document_document jdd
              ON jdd.from_document_id = ac.document_id
             AND jdd.type = 'scene'
            JOIN documents d
              ON d.id = jdd.to_document_id
             AND d.type = 'scene'
             AND (d.json_data->>'start_frame')::int = ac.start_position
            WHERE ac.id = ANY(:chunk_ids)
        """), {"chunk_ids": chunk_ids}).fetchall()
        return {row.chunk_id: row.scene_doc_id for row in rows}
    finally:
        db.close()


def fetch_keyframes_by_scene_ids(scene_doc_ids: list[int]) -> dict[int, list]:
    """Retourne {scene_doc_id: [{rank, frame_index, blob_url}]}."""
    if not scene_doc_ids:
        return {}
    db = next(get_db())
    try:
        rows = db.execute(text("""
            SELECT
                jdd.from_document_id                             AS scene_doc_id,
                COALESCE((d.json_data->>'rank')::int,        0)  AS rank,
                COALESCE((d.json_data->>'frame_index')::int, 0)  AS frame_index,
                d.blob_url                                       AS blob_url
            FROM join_document_document jdd
            JOIN documents d ON d.id = jdd.to_document_id
            WHERE jdd.type = 'keyframe'
              AND jdd.from_document_id = ANY(:ids)
            ORDER BY jdd.from_document_id, rank
        """), {"ids": scene_doc_ids}).fetchall()

        result: dict[int, list] = {}
        for row in rows:
            sid = row.scene_doc_id
            if sid not in result:
                result[sid] = []
            result[sid].append({
                "rank":        row.rank,
                "frame_index": row.frame_index,
                "blob_url":    row.blob_url,
            })
        return result
    finally:
        db.close()


def load_reprise_network_from_db(analyse_id: int) -> dict:
    """
    Reconstruit le réseau de reprises depuis join_chunk_chunk (type='coordination').

    Structure retournée :
      nodes  : {key: {doc_id, scene_indices}}
      edges  : [{source, target, source_scenes, target_scenes,
                 best_distance, length, is_exact_reprise}]
    Les scenes dans edges ont : {scene_doc_id, scene_index, frame_index}.
    Les métadonnées enrichies (transcript, keyframes) sont ajoutées ensuite
    par la route via fetch_scene_metadata / fetch_keyframes_by_scene_ids.
    """
    db = next(get_db())
    try:
        rows = db.execute(text("""
            SELECT
                jcc.from_chunk_id,
                jcc.to_chunk_id,
                jcc.similarity,
                jcc.json_data,
                ac_a.document_id    AS video_id_a,
                ac_b.document_id    AS video_id_b,
                ac_a.start_position AS start_frame_a,
                ac_b.start_position AS start_frame_b
            FROM join_chunk_chunk jcc
            JOIN analyses_chunks ac_a ON ac_a.id = jcc.from_chunk_id
            JOIN analyses_chunks ac_b ON ac_b.id = jcc.to_chunk_id
            WHERE jcc.type      = 'coordination'
              AND ac_a.analyse_id = :analyse_id
        """), {"analyse_id": analyse_id}).fetchall()
    finally:
        db.close()

    if not rows:
        return {"nodes": {}, "edges": [], "noise_groups": [], "source": "db"}

    ## Regrouper par paire (video_a, seq_a, video_b, seq_b).
    ## Nouvelles données  : seq_a/seq_b contiennent des chunk_ids.
    ##   from_chunk_id = min(chunk_a, chunk_b), donc from_chunk_id ∈ seq_a ou seq_b.
    ##   On détermine la direction en vérifiant l'appartenance.
    ## Données existantes : seq_a/seq_b contiennent des start_frames (petits entiers).
    ##   from_chunk_id (grand entier) n'est dans aucune seq → fallback start_frame.
    edge_map: dict = {}
    for row in rows:
        jd        = row.json_data or {}
        seq_a_raw = tuple(jd.get("seq_a", [row.from_chunk_id]))
        seq_b_raw = tuple(jd.get("seq_b", [row.to_chunk_id]))
        set_a     = set(seq_a_raw)
        set_b     = set(seq_b_raw)

        if row.from_chunk_id in set_a:
            seq_for_a, seq_for_b = seq_a_raw, seq_b_raw
            scene_key_a, scene_key_b = row.from_chunk_id, row.to_chunk_id
        elif row.from_chunk_id in set_b:
            seq_for_a, seq_for_b = seq_b_raw, seq_a_raw
            scene_key_a, scene_key_b = row.from_chunk_id, row.to_chunk_id
        else:
            ## Données existantes : seq contient des start_frames
            if row.start_frame_a in set_b and row.start_frame_a not in set_a:
                seq_for_a, seq_for_b = seq_b_raw, seq_a_raw
            else:
                seq_for_a, seq_for_b = seq_a_raw, seq_b_raw
            scene_key_a, scene_key_b = row.start_frame_a, row.start_frame_b

        key = (row.video_id_a, seq_for_a, row.video_id_b, seq_for_b)

        if key not in edge_map:
            edge_map[key] = {
                "video_id_a":   row.video_id_a,
                "video_id_b":   row.video_id_b,
                "seq_a":        seq_for_a,
                "seq_b":        seq_for_b,
                "length":       len(seq_for_a),
                "similarities": [],
                "scenes_a":     {},
                "scenes_b":     {},
            }

        edge_map[key]["similarities"].append(row.similarity)
        edge_map[key]["scenes_a"][scene_key_a] = {
            "chunk_id":    row.from_chunk_id,
            "frame_index": row.start_frame_a,
        }
        edge_map[key]["scenes_b"][scene_key_b] = {
            "chunk_id":    row.to_chunk_id,
            "frame_index": row.start_frame_b,
        }

    def _node_key(video_id: int, seq_frames) -> str:
        return str(video_id) + ":" + ",".join(str(f) for f in seq_frames)

    nodes: dict = {}
    edges: list = []

    for entry in edge_map.values():
        src_key = _node_key(entry["video_id_a"], entry["seq_a"])
        tgt_key = _node_key(entry["video_id_b"], entry["seq_b"])

        scenes_a = [entry["scenes_a"][f] for f in entry["seq_a"] if f in entry["scenes_a"]]
        scenes_b = [entry["scenes_b"][f] for f in entry["seq_b"] if f in entry["scenes_b"]]

        nodes.setdefault(src_key, {"doc_id": entry["video_id_a"]})
        nodes.setdefault(tgt_key, {"doc_id": entry["video_id_b"]})

        best_sim      = max(entry["similarities"]) if entry["similarities"] else 0.0
        best_distance = round(1.0 - best_sim, 4)

        edges.append({
            "source":           src_key,
            "target":           tgt_key,
            "source_scenes":    scenes_a,
            "target_scenes":    scenes_b,
            "best_distance":    best_distance,
            "length":           entry["length"],
            "is_exact_reprise": False,
        })

    edges.sort(key=lambda e: (-e["length"], e["best_distance"]))
    return {"nodes": nodes, "edges": edges, "noise_groups": [], "source": "db"}


# =============================================================================
# GCS / FICHIERS
# =============================================================================

def blob_url_to_key(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:16]


def cleanup_old_work_dirs(current_key=None):
    now = time.time()
    for entry in os.listdir(WORK_DIR):
        path = os.path.join(WORK_DIR, entry)
        if not os.path.isdir(path):
            continue
        if current_key and entry == current_key:
            continue
        try:
            if now - os.path.getmtime(path) > WORK_DIR_MAX_AGE:
                shutil.rmtree(path, ignore_errors=True)
        except FileNotFoundError:
            continue


def download_gcs(gs_url: str, local_path: str) -> None:
    parsed = urlparse(gs_url)
    bucket = get_storage_client().bucket(parsed.netloc)
    bucket.blob(parsed.path.lstrip("/")).download_to_filename(local_path)


def ensure_keyframes(scenes: list[dict], work_dir: str, kf_by_scene: dict[int, list]) -> None:
    """Télécharge les keyframes manquantes depuis GCS (nommage : scene{sid}_kf{rank}.jpg)."""
    import logging
    for scene in scenes:
        sid = scene.get("scene_doc_id")
        if not sid:
            continue
        for kf in kf_by_scene.get(sid, []):
            local = os.path.join(work_dir, f"scene{sid}_kf{kf['rank']}.jpg")
            if not os.path.exists(local) and kf.get("blob_url"):
                try:
                    download_gcs(kf["blob_url"], local)
                except Exception as e:
                    logging.warning(f"Keyframe download failed scene={sid} rank={kf['rank']}: {e}")


# =============================================================================
# ENRICHISSEMENT SCÈNES DEPUIS LA BDD
# =============================================================================

def enrich_scene(
    scene_data: dict,
    scene_meta: dict[int, dict],
    kf_by_scene: dict[int, list],
) -> dict | None:
    """
    Enrichit une scène avec les métadonnées BDD.
    Supprime l'embedding numpy si présent.
    """
    if not isinstance(scene_data, dict):
        return None
    sid  = scene_data.get("scene_doc_id")
    meta = scene_meta.get(sid, {}) if sid else {}
    return {
        "scene_doc_id": sid,
        "scene_index":  meta.get("scene_index", scene_data.get("scene_index")),
        "transcript":   meta.get("transcript", ""),
        "start_ms":     meta.get("start_ms", 0),
        "end_ms":       meta.get("end_ms", 0),
        "raw_norm":     scene_data.get("raw_norm"),
        "keyframes":    kf_by_scene.get(sid, []) if sid else [],
    }


def enrich_scenes(raw_list: list, scene_meta: dict, kf_by_scene: dict) -> list:
    return [s for raw in raw_list if isinstance(raw, dict)
            for s in [enrich_scene(raw, scene_meta, kf_by_scene)] if s]


def build_groups_from_edges(enriched_edges: list, nodes: dict) -> list:
    """
    Regroupe les arêtes en composantes connexes via DSU.
    Un groupe = ensemble de vidéos partageant toutes la même séquence.
    """
    from collections import defaultdict

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for edge in enriched_edges:
        union(edge["source"], edge["target"])

    ## Regrouper arêtes et scènes par composante
    comp_edges:  dict[str, list] = defaultdict(list)
    scenes_by_node: dict[str, list] = {}
    for edge in enriched_edges:
        comp_edges[find(edge["source"])].append(edge)
        if edge["source"] not in scenes_by_node:
            scenes_by_node[edge["source"]] = edge.get("source_scenes", [])
        if edge["target"] not in scenes_by_node:
            scenes_by_node[edge["target"]] = edge.get("target_scenes", [])

    groups = []
    for comp, comp_edge_list in comp_edges.items():
        node_keys = {key for edge in comp_edge_list for key in (edge["source"], edge["target"])}

        members = sorted(
            [
                {
                    "doc_id": nodes.get(key, {}).get("doc_id"),
                    "scenes": scenes_by_node.get(key, []),
                }
                for key in node_keys
            ],
            key=lambda m: m["doc_id"] or 0,
        )

        groups.append({
            "length":           comp_edge_list[0]["length"] if comp_edge_list else 0,
            "doc_count":        len(node_keys),
            "best_distance":    min(e["best_distance"] for e in comp_edge_list),
            "is_exact_reprise": any(e.get("is_exact_reprise") for e in comp_edge_list),
            "members":          members,
            "n_edges":          len(comp_edge_list),
        })

    groups.sort(key=lambda g: (-g["doc_count"], -g["length"], g["best_distance"]))
    return groups


# =============================================================================
# ROUTES
# =============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/reprises")
def reprises():
    analyse_id_str = request.args.get("analyse_id", "").strip()

    if not analyse_id_str:
        return render_template("reprises.html",
            groups=None, noise_groups=None, n_nodes=0, n_edges=0,
            analyse_id="", reprise_work_key="", error=None)

    analyse_id = int(analyse_id_str)
    error      = None
    network    = None

    try:
        network = load_reprise_network_from_db(analyse_id)
    except Exception as e:
        error = f"Erreur BDD : {e}"

    if network is None:
        return render_template("reprises.html",
            groups=None, noise_groups=None, n_nodes=0, n_edges=0,
            analyse_id=analyse_id_str, reprise_work_key="", error=error)

    nodes        = network.get("nodes", {})
    raw_edges    = network.get("edges", [])
    noise_groups = network.get("noise_groups", [])

    ## Résolution chunk_id → scene_doc_id (requête dédiée, plus fiable que les LEFT JOINs)
    all_chunk_ids: list[int] = list({
        scene["chunk_id"]
        for edge in raw_edges
        for scene in edge.get("source_scenes", []) + edge.get("target_scenes", [])
        if isinstance(scene, dict) and scene.get("chunk_id")
    })
    chunk_to_scene = fetch_scene_doc_ids_by_chunk_ids(all_chunk_ids)
    for edge in raw_edges:
        for scene in edge.get("source_scenes", []) + edge.get("target_scenes", []):
            if isinstance(scene, dict):
                scene["scene_doc_id"] = chunk_to_scene.get(scene["chunk_id"])

    ## Enrichissement BDD : transcripts + keyframes
    all_scene_doc_ids: list[int] = [sid for sid in chunk_to_scene.values() if sid]
    scene_meta  = fetch_scene_metadata(all_scene_doc_ids)
    kf_by_scene = fetch_keyframes_by_scene_ids(all_scene_doc_ids)

    ## Préparer le répertoire de travail + télécharger keyframes
    reprise_work_key = blob_url_to_key(str(analyse_id))
    reprise_work_dir = os.path.join(WORK_DIR, reprise_work_key)
    os.makedirs(reprise_work_dir, exist_ok=True)
    cleanup_old_work_dirs(current_key=reprise_work_key)

    all_scenes_flat = [
        s
        for edge in raw_edges
        for raw in edge.get("source_scenes", []) + edge.get("target_scenes", [])
        if isinstance(raw, dict)
        for s in [enrich_scene(raw, scene_meta, kf_by_scene)] if s
    ]
    ensure_keyframes(all_scenes_flat, reprise_work_dir, kf_by_scene)

    ## Construire les arêtes enrichies puis regrouper par composantes connexes
    enriched_edges = [
        {
            "source":           edge["source"],
            "target":           edge["target"],
            "length":           edge.get("length"),
            "best_distance":    edge.get("best_distance"),
            "is_exact_reprise": edge.get("is_exact_reprise", False),
            "source_scenes":    enrich_scenes(edge.get("source_scenes", []), scene_meta, kf_by_scene),
            "target_scenes":    enrich_scenes(edge.get("target_scenes", []), scene_meta, kf_by_scene),
        }
        for edge in raw_edges
    ]
    groups = build_groups_from_edges(enriched_edges, nodes)

    return render_template("reprises.html",
        groups=groups, noise_groups=noise_groups,
        n_nodes=len(nodes), n_edges=len(raw_edges),
        n_groups=len(groups),
        analyse_id=analyse_id_str,
        reprise_work_key=reprise_work_key,
        error=error)


@app.route("/media/<work_key>/<path:filename>")
def serve_media(work_key, filename):
    """Sert les fichiers keyframes téléchargés localement."""
    work_dir = os.path.join(WORK_DIR, work_key)
    return send_from_directory(work_dir, filename, conditional=True)


# =============================================================================

if __name__ == "__main__":
    app.run(debug=True, port=5002, host="0.0.0.0")
