"""
Application de visualisation des transcript chunks.

Lit les données de transcription directement depuis la base de données
(json_data["scene_transcription"]) — aucun calcul local.

Endpoints :
  GET /                                         → formulaire de saisie
  GET /chunks?doc_id=<id>                       → chunks depuis la BDD
  GET /chunks?url=gs://...&importation_id=<id>  → idem via blob_url
  GET /media/<work_key>/video                   → sert la vidéo (téléchargée depuis GCS)

Lancement :
  cd notebooks/video-mining/transcript-chunks-app
  python app.py
"""

import hashlib
import logging
import os
import shutil
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, send_file, url_for
from google.cloud import storage
from sqlalchemy import select

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

## Injecter les vars BDD avant le premier appel à get_settings() (cache lru_cache)
from gcpOpsci.settings import get_settings
get_settings.cache_clear()

from gcpOpsci.db import get_db
from gcpOpsci.models import Document


# =============================================================================
# CONFIG
# =============================================================================

WORK_DIR = "/tmp/chunks_app"
WORK_DIR_MAX_AGE_SECONDS = 3600 // 2

os.makedirs(WORK_DIR, exist_ok=True)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_storage_client = None


def get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


# =============================================================================
# NORMALISATION DES SCÈNES
# =============================================================================

def normalize_scene(scene: dict) -> dict:
    """Assure la compatibilité entre les différents formats de scène stockés en base."""
    s = dict(scene)
    if "scene_index" not in s:
        s["scene_index"] = s.get("index", 0)
    if "start_seconds" not in s:
        s["start_seconds"] = s.get("start_ms", 0) / 1000.0
    if "end_seconds" not in s:
        s["end_seconds"] = s.get("end_ms", 0) / 1000.0
    return s


# =============================================================================
# FILTRES JINJA
# =============================================================================

@app.template_filter("ms_to_time")
def ms_to_time_filter(ms: int) -> str:
    s = ms / 1000
    minutes = int(s // 60)
    seconds = s % 60
    return f"{minutes:02d}:{seconds:04.1f}"


@app.template_filter("ms_to_seconds")
def ms_to_seconds_filter(ms: int) -> float:
    return ms / 1000


@app.template_filter("basename")
def basename_filter(path: str) -> str:
    return os.path.basename(path)


# =============================================================================
# HELPERS BDD
# =============================================================================

def _row_to_result(row) -> tuple[int, str, dict] | tuple[None, None, None]:
    if not row:
        return None, None, None
    return row[0], row[1], row[2]


def fetch_document_by_id(doc_id: int) -> tuple[int | None, str | None, dict | None]:
    db = next(get_db())
    try:
        row = db.execute(
            select(Document.id, Document.blob_url, Document.json_data)
            .where(Document.id == doc_id)
        ).first()
        return _row_to_result(row)
    finally:
        db.close()


def fetch_document_by_url(
    blob_url: str,
    importation_id: int | None = None,
) -> tuple[int | None, str | None, dict | None]:
    db = next(get_db())
    try:
        q = select(Document.id, Document.blob_url, Document.json_data).where(
            Document.blob_url == blob_url
        )
        if importation_id is not None:
            q = q.where(Document.importation_id == importation_id)
        row = db.execute(q).first()
        return _row_to_result(row)
    finally:
        db.close()


def resolve_document(
    doc_id_str: str,
    blob_url: str,
    importation_id_str: str,
) -> tuple[int | None, str | None, dict | None]:
    """Deux modes exclusifs :
    - doc_id seul      → recherche par clé primaire
    - url + importation_id → recherche par blob_url
    """
    if doc_id_str:
        return fetch_document_by_id(int(doc_id_str))
    if blob_url and importation_id_str:
        return fetch_document_by_url(blob_url, int(importation_id_str))
    return None, None, None


# =============================================================================
# HELPERS GCS / FICHIERS
# =============================================================================

def blob_url_to_key(blob_url: str) -> str:
    return hashlib.md5(blob_url.encode()).hexdigest()[:16]


def cleanup_old_work_dirs(
    current_key: str | None = None,
    max_age_seconds: int = WORK_DIR_MAX_AGE_SECONDS,
) -> None:
    now = time.time()
    for entry in os.listdir(WORK_DIR):
        entry_path = os.path.join(WORK_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        if current_key is not None and entry == current_key:
            continue
        try:
            if now - os.path.getmtime(entry_path) > max_age_seconds:
                shutil.rmtree(entry_path, ignore_errors=True)
        except FileNotFoundError:
            continue


def prepare_work_dir(blob_url: str) -> tuple[str, str]:
    """Crée le répertoire de travail et y enregistre le blob_url pour le serving vidéo."""
    work_key = blob_url_to_key(blob_url)
    cleanup_old_work_dirs(current_key=work_key)
    work_dir = os.path.join(WORK_DIR, work_key)
    os.makedirs(work_dir, exist_ok=True)
    ref_path = os.path.join(work_dir, "blob_url.txt")
    if not os.path.exists(ref_path):
        with open(ref_path, "w") as f:
            f.write(blob_url)
    return work_key, work_dir


def download_gcs(gs_url: str, local_path: str) -> str:
    parsed = urlparse(gs_url)
    bucket = get_storage_client().bucket(parsed.netloc)
    bucket.blob(parsed.path.lstrip("/")).download_to_filename(local_path)
    return local_path


def ensure_video(work_dir: str) -> str:
    """Télécharge la vidéo depuis GCS si elle n'est pas encore en cache local."""
    ref_path = os.path.join(work_dir, "blob_url.txt")
    if not os.path.exists(ref_path):
        raise FileNotFoundError("blob_url.txt introuvable dans le work_dir")
    with open(ref_path) as f:
        blob_url = f.read().strip()
    local_path = os.path.join(work_dir, "original.mp4")
    if not os.path.exists(local_path):
        logging.info(f"📥 Téléchargement vidéo depuis GCS : {blob_url}")
        download_gcs(blob_url, local_path)
    return local_path


# =============================================================================
# HELPERS ROUTES
# =============================================================================

def _get_request_params() -> tuple[str, str, str]:
    return (
        request.args.get("doc_id", "").strip(),
        request.args.get("url", "").strip(),
        request.args.get("importation_id", "").strip(),
    )


def _build_reload_url(doc_id_str: str, blob_url: str, importation_id_str: str) -> str:
    if doc_id_str:
        return f"/chunks?doc_id={doc_id_str}"
    return f"/chunks?url={blob_url}&importation_id={importation_id_str}"


def load_document_or_error():
    doc_id_str, blob_url, importation_id_str = _get_request_params()

    if not doc_id_str and not (blob_url and importation_id_str):
        return None, None, None, None, redirect(url_for("index"))

    doc_id, resolved_blob_url, json_data = resolve_document(doc_id_str, blob_url, importation_id_str)
    if not json_data:
        return None, None, None, None, (
            render_template(
                "error.html",
                message=f"Document non trouvé (doc_id={doc_id_str or '—'}, url={blob_url or '—'})",
            ),
            404,
        )

    if not json_data.get("transcript_chunks"):
        return None, None, None, None, (
            render_template(
                "error.html",
                message=(
                    f"Aucune transcription disponible pour ce document (doc_id={doc_id}).\n"
                    "Lancez d'abord video-scene-detection puis video-transcription."
                ),
            ),
            422,
        )

    return doc_id, resolved_blob_url, json_data, {
        "doc_id_str": doc_id_str,
        "importation_id_str": importation_id_str,
        "blob_url": blob_url,
    }, None


# =============================================================================
# ROUTES
# =============================================================================

@app.route("/")
def index():
    doc_id_str, blob_url, importation_id_str = _get_request_params()
    if doc_id_str:
        return redirect(url_for("chunks") + "?" + request.query_string.decode())
    if blob_url and importation_id_str:
        return redirect(url_for("chunks") + "?" + request.query_string.decode())
    if blob_url and not importation_id_str:
        return render_template(
            "error.html",
            message="URL GCS fournie sans importation_id — les deux sont obligatoires ensemble.",
        ), 400
    return render_template("index.html")


@app.route("/chunks")
def chunks():
    doc_id, blob_url, json_data, params, error_response = load_document_or_error()
    if error_response:
        return error_response

    scene_transcription = json_data.get("scene_transcription") or {}
    chunks_data   = json_data.get("transcript_chunks", [])
    scenes        = [normalize_scene(s) for s in scene_transcription.get("scenes", [])]
    speech_units  = []
    for chunk in chunks_data: 
        speech_units += chunk["speech_unit_indices"]
        
    detected_lang = scene_transcription.get("language", "?")
    metadata      = json_data.get("video_metadata", {})
    processed_at  = scene_transcription.get("transcribed_at", "")
    model_name    = scene_transcription.get("model", "")

    work_key, _ = prepare_work_dir(blob_url)
    reload_url  = _build_reload_url(params["doc_id_str"], blob_url, params["importation_id_str"])

    return render_template(
        "chunks.html",
        video_path=blob_url,
        model_name=model_name,
        lang_param=detected_lang,
        cache_key=work_key,
        reload_url=reload_url,
        chunks=chunks_data,
        speech_units=speech_units,
        scenes=scenes,
        metadata=metadata,
        detected_lang=detected_lang,
        processed_at=processed_at,
        doc_id=doc_id,
        doc_id_str=params["doc_id_str"],
        importation_id_str=params["importation_id_str"],
        blob_url=blob_url,
    )


@app.route("/media/<work_key>/video")
def serve_video(work_key):
    """Sert la vidéo via range requests pour le lecteur HTML5 (téléchargement GCS à la demande)."""
    work_dir = os.path.join(WORK_DIR, work_key)
    if not os.path.isdir(work_dir):
        return "Work dir introuvable", 404
    try:
        local_path = ensure_video(work_dir)
    except Exception as e:
        logging.error(f"❌ Erreur téléchargement vidéo : {e}")
        return f"Erreur : {e}", 500
    return send_file(local_path, mimetype="video/mp4", conditional=True)


# =============================================================================

if __name__ == "__main__":
    app.run(debug=True, port=5002, host="0.0.0.0")
