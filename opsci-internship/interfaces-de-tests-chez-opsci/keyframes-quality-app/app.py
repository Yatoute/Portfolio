"""
Application de visualisation des keyframes vidéo.

Endpoints :
  GET /keyframes?doc_id=12345
  GET /keyframes?url=gs://...&importation_id=1070
  GET /video?doc_id=12345

Lancement :
  cd notebooks/video-mining/keyframes-quality-app
  python app.py
"""

import hashlib
import os
import shutil
import subprocess
import time
from urllib.parse import urlparse

import cv2
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, send_from_directory, url_for
from google.cloud import storage
from sqlalchemy import select, text

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

## Injecter les vars BDD avant le premier appel à get_settings() (cache lru_cache)
from gcpOpsci.settings import get_settings
get_settings.cache_clear()

from gcpOpsci.db import get_db
from gcpOpsci.models import Document


# =============================================================================
# CONFIG
# =============================================================================

WORK_DIR = "/tmp/kf_app"
WORK_DIR_MAX_AGE_SECONDS = 3600 // 2

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

def fetch_document_by_id(doc_id: int) -> tuple[int | None, str | None, dict | None]:
    """Recherche par documents.id — le plus rapide (clé primaire)."""
    db = next(get_db())
    try:
        row = db.execute(
            select(Document.id, Document.blob_url, Document.json_data)
            .where(Document.id == doc_id)
        ).first()
        return (row[0], row[1], row[2]) if row else (None, None, None)
    finally:
        db.close()


def fetch_document_by_url(
    blob_url: str,
    importation_id: int | None = None,
) -> tuple[int | None, str | None, dict | None]:
    """Recherche par blob_url + importation_id optionnel."""
    db = next(get_db())
    try:
        q = select(Document.id, Document.blob_url, Document.json_data).where(
            Document.blob_url == blob_url
        )
        if importation_id is not None:
            q = q.where(Document.importation_id == importation_id)
        row = db.execute(q).first()
        return (row[0], row[1], row[2]) if row else (None, None, None)
    finally:
        db.close()


def resolve_document(doc_id_str, blob_url, importation_id_str):
    if doc_id_str:
        return fetch_document_by_id(int(doc_id_str))
    if blob_url and importation_id_str:
        return fetch_document_by_url(blob_url, int(importation_id_str))
    return None, None, None


def fetch_scenes_with_keyframes(video_id: int) -> tuple[list, dict]:
    """
    Charge les scènes et leurs keyframes depuis la BDD pour un document vidéo.

    Scènes : join_document_document (from=video → to=scene, type='scene')
    Keyframes : join_document_document (from=scene → to=keyframe, type='keyframe')

    Retourne (scenes, video_meta).
    """
    db = next(get_db())
    try:
        rows = db.execute(text("""
            SELECT
                d_scene.id                                              AS scene_doc_id,
                COALESCE((d_scene.json_data->>'scene_index')::int, 0)   AS scene_index,
                COALESCE((d_scene.json_data->>'start_ms')::int,    0)   AS start_ms,
                COALESCE((d_scene.json_data->>'end_ms')::int,      0)   AS end_ms,
                COALESCE((d_scene.json_data->>'start_frame')::int,  0)  AS start_frame,
                COALESCE((d_scene.json_data->>'end_frame')::int,    0)  AS end_frame,
                d_scene.text                                            AS transcript,
                d_kf.id                                                 AS kf_doc_id,
                COALESCE((d_kf.json_data->>'rank')::int,        0)      AS kf_rank,
                COALESCE((d_kf.json_data->>'frame_index')::int, 0)      AS kf_frame_index,
                d_kf.blob_url                                           AS kf_blob_url
            FROM join_document_document jdd_scene
            JOIN documents d_scene
              ON d_scene.id   = jdd_scene.to_document_id
             AND d_scene.type = 'scene'
            LEFT JOIN join_document_document jdd_kf
              ON jdd_kf.from_document_id = d_scene.id
             AND jdd_kf.type = 'keyframe'
            LEFT JOIN documents d_kf ON d_kf.id = jdd_kf.to_document_id
            WHERE jdd_scene.from_document_id = :video_id
              AND jdd_scene.type = 'scene'
            ORDER BY scene_index, kf_rank
        """), {"video_id": video_id}).fetchall()

        video_row = db.execute(
            select(Document.json_data).where(Document.id == video_id)
        ).first()
        video_meta = (video_row[0] or {}).get("video_metadata", {}) if video_row else {}
        fps = float(video_meta.get("fps", 25.0))

        scenes_dict: dict = {}
        for row in rows:
            sid = row.scene_doc_id
            if sid not in scenes_dict:
                scenes_dict[sid] = {
                    "scene_doc_id":  sid,
                    "scene_index":   row.scene_index,
                    "start_ms":      row.start_ms,
                    "end_ms":        row.end_ms,
                    "start_frame":   row.start_frame,
                    "end_frame":     row.end_frame,
                    "start_seconds": round(row.start_ms / 1000.0, 3),
                    "end_seconds":   round(row.end_ms / 1000.0, 3),
                    "transcript":    row.transcript or "",
                    "keyframes":     [],
                }
            if row.kf_blob_url:
                scenes_dict[sid]["keyframes"].append({
                    "rank":              row.kf_rank,
                    "frame_index":       row.kf_frame_index,
                    "blob_url":          row.kf_blob_url,
                    "timestamp_seconds": round(row.kf_frame_index / fps, 2),
                })

        scenes = sorted(scenes_dict.values(), key=lambda s: s["scene_index"])
        return scenes, video_meta

    finally:
        db.close()


# =============================================================================
# GCS / FICHIERS
# =============================================================================

def blob_url_to_key(blob_url: str) -> str:
    return hashlib.md5(blob_url.encode()).hexdigest()[:16]


def cleanup_old_work_dirs(current_key=None, max_age_seconds=WORK_DIR_MAX_AGE_SECONDS):
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
    work_key = blob_url_to_key(blob_url)
    cleanup_old_work_dirs(current_key=work_key)
    work_dir = os.path.join(WORK_DIR, work_key)
    os.makedirs(work_dir, exist_ok=True)
    return work_key, work_dir


def download_gcs(gs_url: str, local_path: str) -> str:
    parsed = urlparse(gs_url)
    bucket = get_storage_client().bucket(parsed.netloc)
    bucket.blob(parsed.path.lstrip("/")).download_to_filename(local_path)
    return local_path


def ensure_keyframes(scenes: list, work_dir: str) -> None:
    """Télécharge les keyframes manquants depuis GCS (nommage : s{idx}_kf{rank}.jpg)."""
    for scene in scenes:
        idx = scene["scene_index"]
        for kf in scene.get("keyframes", []):
            local = os.path.join(work_dir, f"s{idx}_kf{kf['rank']}.jpg")
            if not os.path.exists(local) and kf.get("blob_url"):
                download_gcs(kf["blob_url"], local)




# =============================================================================
# RECONSTRUCTION VIDÉO
# =============================================================================

def build_reconstructed_video(scenes, original_path, work_dir, fps=25.0):
    output = os.path.join(work_dir, "reconstructed.mp4")
    if os.path.exists(output):
        return output

    cap = cv2.VideoCapture(original_path)
    min_dur = 1.0 / max(fps, 1.0)

    def ts_of(frame_idx):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        return cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

    scene_clips = []
    try:
        for scene in sorted(scenes, key=lambda s: s["scene_index"]):
            idx = scene["scene_index"]
            kfs = sorted(scene.get("keyframes", []), key=lambda x: x["rank"])
            if not kfs:
                continue

            scene_start_ts = ts_of(scene["start_frame"])
            scene_end_ts   = ts_of(scene["end_frame"])
            kf_ts = [ts_of(kf["frame_index"]) for kf in kfs]

            kf_durations = []
            for i, ts in enumerate(kf_ts):
                if i == 0:
                    dur = (kf_ts[1] if len(kf_ts) > 1 else scene_end_ts) - scene_start_ts
                elif i < len(kf_ts) - 1:
                    dur = kf_ts[i + 1] - ts
                else:
                    dur = scene_end_ts - ts
                kf_durations.append(max(dur, min_dur))

            concat_images = os.path.join(work_dir, f"s{idx}_imgs.txt")
            with open(concat_images, "w") as f:
                for kf, dur in zip(kfs, kf_durations):
                    kf_path = os.path.join(work_dir, f"s{idx}_kf{kf['rank']}.jpg")
                    f.write(f"file '{kf_path}'\nduration {dur:.6f}\n")
                last_kf_path = os.path.join(work_dir, f"s{idx}_kf{kfs[-1]['rank']}.jpg")
                f.write(f"file '{last_kf_path}'\n")

            slideshow = os.path.join(work_dir, f"s{idx}_slide.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_images,
                "-vf", f"fps={fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-t", f"{sum(kf_durations):.6f}", slideshow,
            ], check=True, capture_output=True)

            audio = os.path.join(work_dir, f"s{idx}_audio.aac")
            ar = subprocess.run([
                "ffmpeg", "-y", "-i", original_path,
                "-ss", f"{scene_start_ts:.6f}", "-to", f"{scene_end_ts:.6f}",
                "-vn", "-c:a", "aac", audio,
            ], capture_output=True)
            has_audio = ar.returncode == 0 and os.path.exists(audio) and os.path.getsize(audio) > 100

            scene_out = os.path.join(work_dir, f"s{idx}_final.mp4")
            if has_audio:
                subprocess.run([
                    "ffmpeg", "-y", "-i", slideshow, "-i", audio,
                    "-c:v", "copy", "-c:a", "aac", "-shortest", scene_out,
                ], check=True, capture_output=True)
            else:
                shutil.copy(slideshow, scene_out)
            scene_clips.append(scene_out)
    finally:
        cap.release()

    concat_all = os.path.join(work_dir, "all_scenes.txt")
    with open(concat_all, "w") as f:
        for p in scene_clips:
            f.write(f"file '{p}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_all,
        "-c", "copy", output,
    ], check=True, capture_output=True)
    return output


# =============================================================================
# HELPERS ROUTES
# =============================================================================

def _get_request_params():
    return (
        request.args.get("doc_id", "").strip(),
        request.args.get("url", "").strip(),
        request.args.get("importation_id", "").strip(),
    )


def load_document_or_error():
    doc_id_str, blob_url, importation_id_str = _get_request_params()

    if not doc_id_str and not (blob_url and importation_id_str):
        return None, None, None, None, redirect(url_for("index"))

    doc_id, resolved_blob_url, json_data = resolve_document(doc_id_str, blob_url, importation_id_str)
    if doc_id is None:
        return None, None, None, None, (
            render_template("error.html",
                message=f"Document non trouvé (doc_id={doc_id_str or '—'}, url={blob_url or '—'})",
                blob_url=blob_url, doc_id_str=doc_id_str, importation_id_str=importation_id_str),
            404,
        )

    return doc_id, resolved_blob_url, json_data, {
        "doc_id_str": doc_id_str,
        "importation_id_str": importation_id_str,
    }, None


# =============================================================================
# ROUTES
# =============================================================================

@app.route("/")
def index():
    doc_id_str, blob_url, importation_id_str = _get_request_params()
    if doc_id_str or (blob_url and importation_id_str):
        return redirect(url_for("keyframes") + "?" + request.query_string.decode())
    if blob_url and not importation_id_str:
        return render_template("error.html",
            message="URL GCS fournie sans importation_id.",
            blob_url=blob_url, doc_id_str="", importation_id_str=""), 400
    return render_template("index.html")


@app.route("/keyframes")
def keyframes():
    doc_id, blob_url, _json_data, params, error_response = load_document_or_error()
    if error_response:
        return error_response

    scenes, video_meta = fetch_scenes_with_keyframes(doc_id)
    work_key, work_dir = prepare_work_dir(blob_url)
    ensure_keyframes(scenes, work_dir)

    return render_template("keyframes.html",
        blob_url=blob_url, doc_id=doc_id,
        doc_id_str=params["doc_id_str"], importation_id_str=params["importation_id_str"],
        scenes=scenes, video_meta=video_meta, work_key=work_key)


@app.route("/video")
def video():
    doc_id, blob_url, _json_data, params, error_response = load_document_or_error()
    if error_response:
        return error_response

    scenes, video_meta = fetch_scenes_with_keyframes(doc_id)
    fps = float(video_meta.get("fps", 25.0))
    work_key, work_dir = prepare_work_dir(blob_url)
    ensure_keyframes(scenes, work_dir)

    original_path = os.path.join(work_dir, "original.mp4")
    if not os.path.exists(original_path):
        download_gcs(blob_url, original_path)
    build_reconstructed_video(scenes, original_path, work_dir, fps=fps)

    return render_template("video.html",
        blob_url=blob_url, doc_id=doc_id,
        doc_id_str=params["doc_id_str"], importation_id_str=params["importation_id_str"],
        scenes=scenes, video_meta=video_meta, work_key=work_key)


@app.route("/media/<work_key>/<path:filename>")
def serve_media(work_key, filename):
    """Sert les fichiers locaux (vidéos, keyframes) avec support des range requests."""
    work_dir = os.path.join(WORK_DIR, work_key)
    return send_from_directory(work_dir, filename, conditional=True)


# =============================================================================

if __name__ == "__main__":
    app.run(debug=True, port=5001, host="0.0.0.0")
