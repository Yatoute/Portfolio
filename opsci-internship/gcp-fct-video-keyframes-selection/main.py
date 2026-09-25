import gc
import os
import time
import logging
import traceback
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, Future

from typing import Any, Optional, Literal

from flask import Request
from pydantic import BaseModel, Field, field_validator, model_validator

import cv2
import torch
from google.cloud import storage

from gcpOpsci.tools import log_and_return_response, make_task_id, PerfTimer
from gcpOpsci.cloud_tasks import CloudTasks, CloudTask
from gcpOpsci.tools.utils_models import OpsciDefaultRequest

from utils.crud_documents import (
    get_documents_by_ids,
    get_video_documents,
    get_scene_documents_by_video_ids,
    persist_video_keyframes_batch,
    bulk_update_scene_chunk_vectors,
)
from utils.gcs import (
    download_gcs_url_to_tmp,
    cleanup_local_file,
    upload_frame_to_gcs,
)
from utils.keyframes import (
    CLIPTextFrameSelector,
    compute_scene_data,
    select_video_keyframes,
)
from utils.memory_usage import free_rss_memory, log_memory, cleanup_stale_tmp_files

logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


CLOUD_TASK_HANDLER_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-keyframes-selection"
)
CLOUD_TASK_TIMEOUT_SECONDS = 30 * 60
## Marge pour le check pré-vidéo (vidéo complète potentiellement lourde)
CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS = 10 * 60
## Marge plus serrée pour le check post-scène (granularité fine, scènes courtes)
CLOUD_TASK_SCENE_TIMEOUT_SAFETY_MARGIN_SECONDS = 5 * 60


class RequestParams(BaseModel):
    importation_ids: Optional[list[int]] = Field(None, description="Liste d'IDs d'importations. Accepte aussi un int ou une string CSV ('22,696').")
    documents_ids: Optional[list[int]] = Field(
        None,
        description="Liste d'IDs de documents à traiter",
    )

    force_all: Optional[bool] = Field(
        False,
        description="Forcer le retraitement même si les keyframes existent déjà",
    )

    start_date: Optional[datetime.datetime] = Field(
        None,
        description="Date de création des posts (début)",
    )
    end_date: Optional[datetime.datetime] = Field(
        None,
        description="Date de création des posts (fin)",
    )
    inclure_original: Optional[bool] = Field(
        True,
        description="Inclure les posts de type 'original'",
    )
    inclure_comments: Optional[bool] = Field(
        True,
        description="Inclure les posts de type 'comment'",
    )
    inclure_retweets: Optional[bool] = Field(
        True,
        description="Inclure les repartages",
    )
    inclure_citations: Optional[bool] = Field(
        True,
        description="Inclure les posts cités",
    )

    scenes_analyse_id: int = Field(
        ...,
        description="Identifiant de l'analyse (vecteurs de scènes CLIP)",
    )
    keyframes_analyse_id: Optional[int] = Field(
        None,
        description="Identifiant de l'analyse (keyframes vidéo — chunks liés au document vidéo). "
                    "Si None, les chunks keyframes ne sont pas écrits dans analyses_chunks (seuls les embeddings de scènes sont mis à jour).",
    )

    visually_distinct_frame_threshold: Optional[float] = Field(
        0.15,
        description="Seuil Bhattacharyya : utilisé à la fois pour la selection des keyframes"
                    "par scène et pour la déduplication globale des keyframes vidéo",
    )

    target_fps: Optional[float] = Field(
        3.0,
        description="FPS cible pour le sous-échantillonnage temporel",
    )
    max_keyframes_for_scene_embeddings: Optional[int] = Field(
        10,
        description="Nombre maximum de candidats par scène versés dans le pool vidéo",
    )
    max_keyframes_per_video: Optional[int] = Field(
        None,
        description="Nombre maximum de keyframes visuellement distinctes à conserver par vidéo "
                    "(None = pas de limite, seul le seuil Bhattacharyya filtre)",
    )
    max_frames_to_decode: Optional[int] = Field(
        500,
        description="Nombre maximum de frames à décoder par scène (0 = pas de limite). "
                    "Au-delà, les frames intermédiaires sont sautées avec cap.grab() sans décodage.",
    )

    clip_model_name: Optional[str] = Field(
        "ViT-B-32",
        description="Nom du modèle CLIP OpenCLIP",
    )
    clip_pretrained: Optional[str] = Field(
        "openai",
        description="Checkpoint pretrained OpenCLIP",
    )

    device: Optional[Literal["cpu", "cuda", "auto"]] = Field(
        "cpu",
        description="Device de traitement",
    )
    batch_size: Optional[int] = Field(
        64,
        description="Taille des batches pour le découpage en tâches",
    )
    parent_job_id: Optional[str] = Field(
        None,
        description="Identifiant du job parent",
    )
    queue_name: Optional[str] = Field(
        "video-keyframes-selection",
        description="Nom de la file Cloud Tasks",
    )
    keyframes_bucket: Optional[str] = Field(
        "videos-keyframes",
        description="Bucket GCS pour stocker les keyframes",
    )
    call_back_pipeline: Optional[dict] = Field(
        None,
        description="Paramètres du callback vers pipeline-topic-modeling à déclencher par la dernière tâche active.",
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_importation_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "importation_id" in data and "importation_ids" not in data:
                legacy = data.pop("importation_id")
                if legacy is not None:
                    data["importation_ids"] = legacy
        return data

    @field_validator("importation_ids", mode="before")
    @classmethod
    def normalize_importation_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            return [int(v) for v in value if v is not None]
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            if "," in cleaned:
                return [int(x.strip()) for x in cleaned.split(",") if x.strip()]
            return [int(cleaned)]
        return value


CLIP_SELECTOR = None
CLIP_SELECTOR_MODEL_NAME = None
CLIP_SELECTOR_PRETRAINED = None
CLIP_SELECTOR_DEVICE = None


@log_and_return_response
def run(request: Request):
    db = None
    prefetch_executor = None

    try:
        worker_start_time_monotonic = time.monotonic()

        my_request = OpsciDefaultRequest(request, RequestParams)

        db = my_request.db
        params: RequestParams = my_request.params
        storage_client = storage.Client()
        cloud_task_client = CloudTasks()

        logging.info(f"Nouvelle requête avec les paramètres : {params}")
        log_memory("start_job")

        # -------------------------------------------------------------------
        # JOB ID
        # -------------------------------------------------------------------
        if params.parent_job_id is None:
            task_payload = serialize_dates(params.model_dump(exclude_unset=True))
            job_id = make_task_id(task_payload)
            task_payload["parent_job_id"] = job_id

            logging.info(
                f"📸 Appel initial détecté. Dispatching job {job_id} vers Cloud Tasks..."
            )

            task_name = f"a{params.scenes_analyse_id}-j{job_id}-init"

            create_video_keyframes_task(
                cloud_task_client=cloud_task_client,
                queue_name=params.queue_name,
                payload=task_payload,
                task_name=task_name,
                timeout_seconds=CLOUD_TASK_TIMEOUT_SECONDS,
            )

            return {
                "status": "accepted",
                "job_id": job_id,
                "message": (
                    "Job dispatché vers Cloud Tasks. Utilisez job_id pour suivre la progression."
                ),
            }, 202

        job_id = params.parent_job_id
        logging.info(f"Démarrage du job_id : {job_id}")

        retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", 0))
        timer = PerfTimer(function_name="video-keyframes-selection", parent_job_id=job_id, retry_count=retry_count)

        # -------------------------------------------------------------------
        # NORMALISATION DES DATES
        # -------------------------------------------------------------------
        start_date = params.start_date.isoformat() if params.start_date else None
        end_date = params.end_date.isoformat() if params.end_date else None

        # -------------------------------------------------------------------
        # DEVICE
        # -------------------------------------------------------------------
        if params.device == "auto" or params.device is None:
            params.device = "cuda" if torch.cuda.is_available() else "cpu"

        # -------------------------------------------------------------------
        # CHARGEMENT / RÉUTILISATION DU MODÈLE CLIP
        # -------------------------------------------------------------------
        global CLIP_SELECTOR, CLIP_SELECTOR_MODEL_NAME, CLIP_SELECTOR_PRETRAINED, CLIP_SELECTOR_DEVICE

        clip_selector = None

        should_reload_clip = (
            CLIP_SELECTOR is None
            or CLIP_SELECTOR_MODEL_NAME != params.clip_model_name
            or CLIP_SELECTOR_PRETRAINED != params.clip_pretrained
            or CLIP_SELECTOR_DEVICE != params.device
        )
        model_was_cached = not should_reload_clip

        if should_reload_clip:
            if CLIP_SELECTOR is not None:
                logging.info(
                    f"🧹 Releasing previous CLIP selector | "
                    f"old_model={CLIP_SELECTOR_MODEL_NAME} | "
                    f"old_pretrained={CLIP_SELECTOR_PRETRAINED} | "
                    f"old_device={CLIP_SELECTOR_DEVICE}"
                )
                try:
                    old_selector = CLIP_SELECTOR
                    CLIP_SELECTOR = None
                    del old_selector
                except Exception:
                    pass

                gc.collect()
                if params.device == "cuda":
                    torch.cuda.empty_cache()

            logging.info(
                f"📸 Chargement du sélecteur CLIP | "
                f"model={params.clip_model_name} | "
                f"pretrained={params.clip_pretrained} | "
                f"device={params.device}"
            )

            with timer.model_load():
                CLIP_SELECTOR = CLIPTextFrameSelector(
                    model_name=params.clip_model_name,
                    pretrained=params.clip_pretrained,
                    device=params.device,
                )
            CLIP_SELECTOR_MODEL_NAME = params.clip_model_name
            CLIP_SELECTOR_PRETRAINED = params.clip_pretrained
            CLIP_SELECTOR_DEVICE = params.device

        clip_selector = CLIP_SELECTOR

        logging.info(
            f"📸 CLIP loaded : {params.clip_model_name} "
            f"({params.clip_pretrained}) on {params.device}"
        )

        # -------------------------------------------------------------------
        # SCENARIO 1 : documents_ids
        # SCENARIO 2 : importation_id + filtres
        # -------------------------------------------------------------------
        if params.documents_ids:
            logging.info(
                f"📸 Mode Worker : traitement de {len(params.documents_ids)} documents spécifiques."
            )
            with timer.sql_read():
                docs_data = get_documents_by_ids(db, params.documents_ids)

        else:
            if not params.importation_ids:
                timer.emit({"error": "Missing importation_ids or documents_ids"})
                return "Missing importation_ids or documents_ids", 400

            logging.info(
                f"📸 Recherche des vidéos à traiter dans les importations {params.importation_ids}..."
            )

            with timer.sql_read():
                docs_data = get_video_documents(
                    db,
                    importation_ids=params.importation_ids,
                    start_date=start_date,
                    end_date=end_date,
                    include_original=params.inclure_original,
                    include_quotes=params.inclure_citations,
                    include_shares=params.inclure_retweets,
                    include_comments=params.inclure_comments,
                    include_keyframed=params.force_all,
                    scenes_analyse_id=params.scenes_analyse_id,
                    keyframes_analyse_id=params.keyframes_analyse_id,
                )

        if not docs_data:
            if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
                _fire_pipeline_callback(params.call_back_pipeline)
            timer.n_items_read = 0
            timer.emit({"model_was_cached": model_was_cached})
            return {
                "message": "Aucun document vidéo trouvé pour ces critères.",
                "processed_count": 0,
                "job_id": job_id,
            }, 200

        docs_ids_to_process = [doc_id for _, doc_id, _ in docs_data]
        timer.n_items_read = len(docs_ids_to_process)

        # -------------------------------------------------------------------
        # DISPATCH CLOUD TASKS SI BESOIN
        # -------------------------------------------------------------------
        task_batch_limit = params.batch_size or 64

        if len(docs_ids_to_process) > task_batch_limit:
            logging.info(
                f"📸 Dispatching vers Cloud Tasks : {len(docs_ids_to_process)} "
                f"documents à traiter (> {task_batch_limit})"
            )

            batches = [
                docs_ids_to_process[i: i + task_batch_limit]
                for i in range(0, len(docs_ids_to_process), task_batch_limit)
            ]
            task_ids_log = []

            base_payload = serialize_dates(params.model_dump(exclude_unset=True))
            base_payload.pop("importation_id", None)
            base_payload["parent_job_id"] = job_id

            for batch_ids in batches:
                task_payload = {
                    **base_payload,
                    "documents_ids": list(batch_ids),
                }

                t_id = make_task_id(task_payload)
                task_ids_log.append(t_id)

                task_name = f"a{params.scenes_analyse_id}-j{job_id}-t{t_id}"

                create_video_keyframes_task(
                    cloud_task_client=cloud_task_client,
                    queue_name=params.queue_name,
                    payload=task_payload,
                    task_name=task_name,
                    timeout_seconds=CLOUD_TASK_TIMEOUT_SECONDS,
                )

            timer.n_tasks_dispatched = len(task_ids_log)
            timer.emit({"model_was_cached": model_was_cached, "dispatch_type": "batch_split"})
            return {
                "message": (
                    f"Dispatch terminé. {len(task_ids_log)} tâches créées pour "
                    f"{len(docs_ids_to_process)} vidéos."
                ),
                "job_id": job_id,
            }, 200

        # -------------------------------------------------------------------
        # EXECUTION DIRECTE / WORKER
        # -------------------------------------------------------------------
        cleanup_stale_tmp_files(max_age_seconds=600)

        logging.info(f"📸 Exécution pour {len(docs_data)} vidéos...")

        scenes_by_video_id = get_scene_documents_by_video_ids(db, docs_ids_to_process)

        errors = []
        processed_count = 0
        keyframes_batch: list[dict] = []
        vector_updates: list[dict] = []

        prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dl-prefetch")
        prefetch_fut: Optional[Future] = prefetch_executor.submit(
            download_gcs_url_to_tmp, docs_data[0][2], storage_client
        ) if docs_data else None

        for current_index, (importation_id, document_id, blob_url) in enumerate(docs_data):
            local_path = None
            scene_timed_out = False
            doc_blob_id = (blob_url or "").split("/")[-1] or document_id

            logging.info(f"📸 START document_id={document_id} | blob_url={blob_url}")

            # ─── CHECK 1 : timeout AVANT de démarrer la vidéo ──────────────────
            if is_near_cloud_task_timeout(worker_start_time_monotonic, CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS):
                remaining_docs = docs_data[current_index:]

                n_kf = sum(len(v.get("keyframes", [])) for v in keyframes_batch)
                n_vec = len(vector_updates)
                with timer.sql_write(n_items=n_kf + n_vec):
                    persist_video_keyframes_batch(db=db, video_keyframes_batch=keyframes_batch, analyse_id=params.keyframes_analyse_id)
                    bulk_update_scene_chunk_vectors(db=db, vector_updates=vector_updates, analyse_id=params.scenes_analyse_id)
                keyframes_batch = []
                vector_updates = []

                remaining_document_ids = [doc_id for _, doc_id, _ in remaining_docs]
                if remaining_document_ids:
                    next_task_payload = serialize_dates(params.model_dump(exclude_unset=True))
                    next_task_payload.pop("importation_id", None)
                    next_task_payload["parent_job_id"] = job_id
                    next_task_payload["documents_ids"] = remaining_document_ids
                    next_task_id = make_task_id(next_task_payload)
                    next_task_name = f"a{params.scenes_analyse_id}-j{job_id}-resume-{next_task_id}"
                    create_video_keyframes_task(
                        cloud_task_client=cloud_task_client,
                        queue_name=params.queue_name,
                        payload=next_task_payload,
                        task_name=next_task_name,
                        timeout_seconds=CLOUD_TASK_TIMEOUT_SECONDS,
                    )
                    if prefetch_fut is not None:
                        prefetch_fut.cancel()
                    logging.warning(
                        f"⏳ Timeout proche (avant vidéo). Relance planifiée avec "
                        f"{len(remaining_document_ids)} documents | next_task_name={next_task_name}"
                    )
                    timer.n_tasks_dispatched = (timer.n_tasks_dispatched or 0) + 1
                else:
                    ## Aucun document restant → cette tâche est la dernière → callback
                    if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
                        _fire_pipeline_callback(params.call_back_pipeline)

                timer.n_items_written = (timer.n_items_written or 0) + n_kf + n_vec
                timer.emit({"model_was_cached": model_was_cached, "early_return": "timeout_before_video"})
                return {
                    "message": "Succès partiel avant timeout. Reprise planifiée.",
                    "processed_count": processed_count,
                    "failed_count": len(errors),
                    "remaining_count": len(remaining_document_ids),
                    "job_id": job_id,
                }, 200

            try:
                local_path = prefetch_fut.result() if prefetch_fut is not None else download_gcs_url_to_tmp(
                    gs_url=blob_url, storage_client=storage_client
                )
                prefetch_fut = None

                if current_index + 1 < len(docs_data):
                    prefetch_fut = prefetch_executor.submit(
                        download_gcs_url_to_tmp,
                        docs_data[current_index + 1][2],
                        storage_client,
                    )

                scenes = scenes_by_video_id.get(document_id) or []

                if not scenes:
                    raise ValueError(
                        f"Aucune scène disponible en base pour document_id={document_id}"
                    )

                cap = cv2.VideoCapture(local_path)
                if not cap.isOpened():
                    raise FileNotFoundError(
                        f"Impossible d'ouvrir la vidéo : {local_path}"
                    )

                fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                processed_scenes = 0
                ## Pool de candidats visuels pour toutes les scènes de cette vidéo
                video_candidates = []

                try:
                    for scene in scenes:
                        scene_index = scene["scene_index"]

                        scene_for_processing = {
                            "index":       scene_index,
                            "start_frame": scene["start_frame"],
                            "end_frame":   scene["end_frame"],
                            "start_ms":    scene["start_ms"],
                            "end_ms":      scene["end_ms"],
                            "transcript":  scene["transcript"] or "",
                        }

                        ## Collecte des candidats + embedding de scène (pas d'upload ici)
                        with timer.inference():
                            candidates, scene_embedding = compute_scene_data(
                                cap=cap,
                                scene=scene_for_processing,
                                visually_distinct_frame_threshold=params.visually_distinct_frame_threshold,
                                clip_selector=clip_selector,
                                target_fps=params.target_fps,
                                max_candidates=params.max_keyframes_for_scene_embeddings,
                                max_frames_to_decode=params.max_frames_to_decode,
                            )

                        if params.keyframes_analyse_id is not None:
                            video_candidates.extend(candidates)

                        if scene_embedding is not None:
                            vector_updates.append({
                                "video_id":    document_id,
                                "start_frame": scene["start_frame"],
                                "end_frame":   scene["end_frame"],
                                "vector":      scene_embedding.tolist(),
                            })
                            del scene_embedding

                        processed_scenes += 1

                        # ─── CHECK 2 : timeout APRÈS chaque scène ──────────────────
                        if is_near_cloud_task_timeout(worker_start_time_monotonic, CLOUD_TASK_SCENE_TIMEOUT_SAFETY_MARGIN_SECONDS):
                            scene_timed_out = True
                            logging.warning(
                                f"⏳ Timeout proche (après scène {scene_index} / document {document_id}). "
                                f"Arrêt du traitement de la vidéo."
                            )
                            break

                finally:
                    cap.release()

                total_keyframes = 0
                if params.keyframes_analyse_id is not None:
                    ## Déduplication globale sur le pool de la vidéo (complet ou partiel si timeout)
                    selected_kf = select_video_keyframes(
                        video_candidates,
                        visually_distinct_frame_threshold=params.visually_distinct_frame_threshold,
                        max_keyframes=params.max_keyframes_per_video,
                    )
                    del video_candidates

                    ## Upload des keyframes distinctes en parallèle
                    video_keyframes: list[dict] = []
                    if selected_kf:
                        with ThreadPoolExecutor(max_workers=min(len(selected_kf), 12)) as upload_exec:
                            future_to_meta = {
                                upload_exec.submit(
                                    upload_frame_to_gcs,
                                    frame,
                                    params.keyframes_bucket,
                                    f"{importation_id}/{doc_blob_id}/kf_{rank}.jpg",
                                    storage_client,
                                ): (rank, frame_idx, clip_vec)
                                for rank, (frame_idx, frame, clip_vec) in enumerate(selected_kf)
                            }
                            for fut in as_completed(future_to_meta):
                                rank, frame_idx, clip_vec = future_to_meta[fut]
                                video_keyframes.append({
                                    "rank":              rank,
                                    "frame_index":       int(frame_idx),
                                    "blob_url":          fut.result(),
                                    "keyframe_embedding": clip_vec.tolist() if clip_vec is not None else None,
                                })
                        video_keyframes.sort(key=lambda k: k["frame_index"])
                    del selected_kf

                    total_keyframes = len(video_keyframes)
                    keyframes_batch.append({
                        "document_id":  document_id,
                        "importation_id": importation_id,
                        "total_frames": total_frames,
                        "keyframes":    video_keyframes,
                    })
                else:
                    del video_candidates

                if scene_timed_out:
                    logging.info(
                        f"📸 document_id={document_id} | traitement partiel : "
                        f"{processed_scenes}/{len(scenes)} scènes avant timeout."
                    )

                processed_count += 1
                kf_suffix = f" | keyframes={total_keyframes}" if params.keyframes_analyse_id is not None else ""
                logging.info(
                    f"📸 END document_id={document_id} | "
                    f"scenes={processed_scenes}/{len(scenes)}{kf_suffix}"
                    + (" [partiel]" if scene_timed_out else "")
                )

            except Exception as e:
                error_data = {
                    "document_id": document_id,
                    "blob_url": blob_url,
                    "error": f"📸 {str(e)}",
                }
                logging.error(error_data)
                errors.append(error_data)

            finally:
                if local_path:
                    cleanup_local_file(local_path)
                free_rss_memory()

            # ─── CHECK 2 SUITE : timeout détecté mi-scène ──────────────────────
            if scene_timed_out:
                remaining_docs = docs_data[current_index + 1:]

                n_kf2 = sum(len(v.get("keyframes", [])) for v in keyframes_batch)
                n_vec2 = len(vector_updates)
                with timer.sql_write(n_items=n_kf2 + n_vec2):
                    if params.keyframes_analyse_id is not None:
                        persist_video_keyframes_batch(db=db, video_keyframes_batch=keyframes_batch, analyse_id=params.keyframes_analyse_id)
                    bulk_update_scene_chunk_vectors(db=db, vector_updates=vector_updates, analyse_id=params.scenes_analyse_id)
                keyframes_batch = []
                vector_updates = []

                remaining_document_ids = [doc_id for _, doc_id, _ in remaining_docs]
                if remaining_document_ids:
                    next_task_payload = serialize_dates(params.model_dump(exclude_unset=True))
                    next_task_payload.pop("importation_id", None)
                    next_task_payload["parent_job_id"] = job_id
                    next_task_payload["documents_ids"] = remaining_document_ids
                    next_task_id = make_task_id(next_task_payload)
                    next_task_name = f"a{params.scenes_analyse_id}-j{job_id}-resume-{next_task_id}"
                    create_video_keyframes_task(
                        cloud_task_client=cloud_task_client,
                        queue_name=params.queue_name,
                        payload=next_task_payload,
                        task_name=next_task_name,
                        timeout_seconds=CLOUD_TASK_TIMEOUT_SECONDS,
                    )
                    if prefetch_fut is not None:
                        prefetch_fut.cancel()
                    logging.warning(
                        f"⏳ Timeout proche (mi-scène). Relance planifiée avec "
                        f"{len(remaining_document_ids)} documents | next_task_name={next_task_name}"
                    )
                    timer.n_tasks_dispatched = (timer.n_tasks_dispatched or 0) + 1
                else:
                    ## Aucun document restant → cette tâche est la dernière → callback
                    if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
                        _fire_pipeline_callback(params.call_back_pipeline)

                timer.n_items_written = (timer.n_items_written or 0) + n_kf2 + n_vec2
                timer.emit({"model_was_cached": model_was_cached, "early_return": "timeout_after_scene"})
                return {
                    "message": "Succès partiel avant timeout (scène). Reprise planifiée.",
                    "processed_count": processed_count,
                    "failed_count": len(errors),
                    "remaining_count": len(remaining_document_ids),
                    "job_id": job_id,
                }, 200


        n_kf_final = sum(len(v.get("keyframes", [])) for v in keyframes_batch)
        n_vec_final = len(vector_updates)
        with timer.sql_write(n_items=n_kf_final + n_vec_final):
            if params.keyframes_analyse_id is not None:
                persist_video_keyframes_batch(db=db, video_keyframes_batch=keyframes_batch, analyse_id=params.keyframes_analyse_id)
            bulk_update_scene_chunk_vectors(db=db, vector_updates=vector_updates, analyse_id=params.scenes_analyse_id)

        timer.n_items_written = (timer.n_items_written or 0) + n_kf_final + n_vec_final

        if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
            _fire_pipeline_callback(params.call_back_pipeline)

        timer.emit({"model_was_cached": model_was_cached})
        return {
            "message": "Succès",
            "processed_count": processed_count,
            "failed_count": len(errors),
            "job_id": job_id,
        }, 200

    except Exception as e:
        logging.error(f"📸 Critical Error: {e}")
        logging.error(traceback.format_exc())
        if db:
            db.rollback()
        try:
            timer.emit({"error": str(e)})
        except Exception:
            pass
        return f"Error: {e}", 500

    finally:
        if db:
            db.close()
        if prefetch_executor is not None:
            ## Cleanup du fichier téléchargé par le prefetch mais jamais consommé (early return)
            if prefetch_fut is not None:
                prefetch_fut.cancel()
                if not prefetch_fut.cancelled():
                    try:
                        leftover_path = prefetch_fut.result(timeout=30)
                        if leftover_path:
                            cleanup_local_file(leftover_path)
                    except Exception:
                        pass
            prefetch_executor.shutdown(wait=False)
        log_memory("end_of_task")


SCENE_DETECTION_QUEUE = "video-scene-detection"


def _queue_task_count(queue_name: str, parent_job_id: str) -> int:
    """Compte les tâches actives (marqueur 'j{parent_job_id}' dans le nom) d'une queue Cloud Tasks."""
    from google.cloud import tasks_v2
    project_id = os.environ.get("GCLOUD_PROJECT", "demo-project")
    client = tasks_v2.CloudTasksClient()
    queue_path = client.queue_path(project_id, "europe-west1", queue_name)
    job_marker = f"j{parent_job_id}"
    return sum(
        1 for task in client.list_tasks(
            request={"parent": queue_path, "response_view": tasks_v2.Task.View.BASIC}
        )
        if job_marker in task.name.split("/")[-1]
    )


def _is_last_task_in_queue(queue_name: str, parent_job_id: str) -> bool:
    """Vérifie si le video-mining (scene-detection + keyframes-selection) est
    intégralement terminé pour ce job — pas seulement cette queue.

    Toujours dispatché en mode auto_keyframes : scene-detection cascade vers
    keyframes-selection au fil de l'eau. Tant qu'il reste UNE tâche scene-detection
    pour ce job, la cascade peut encore produire de nouvelles tâches keyframes — la
    queue keyframes-selection peut donc paraître vide momentanément sans que le
    mining soit terminé. On ne conclut que si scene-detection est vide ET qu'il ne
    reste qu'une tâche (la nôtre, encore "running") côté keyframes-selection.
    """
    try:
        if _queue_task_count(SCENE_DETECTION_QUEUE, parent_job_id) > 0:
            return False
        active_count = _queue_task_count(queue_name, parent_job_id)
        logging.info(f"📸 Queue check | queue={queue_name} | job={parent_job_id} | active_count={active_count}")
        return active_count <= 1
    except Exception as e:
        logging.warning(f"📸 _is_last_task_in_queue error (assuming not last): {e}")
        return False


def _fire_pipeline_callback(call_back_pipeline: dict) -> None:
    """Crée la Cloud Task de callback vers pipeline-topic-modeling.

    Le task_name fixe garantit la déduplication : si deux tâches parallèles
    tentent de tirer le callback simultanément, une seule réussit.
    """
    try:
        task = CloudTask(
            queue_name=call_back_pipeline["queue_name"],
            name=call_back_pipeline["task_name"],
            http_method="POST",
            url=call_back_pipeline["url"],
            payload=call_back_pipeline["payload"],
            include_oidc_token=True,
            timeout=1800,
        )
        cloud_task_client = CloudTasks()
        cloud_task_client.add_task(task)
        logging.info(f"📸 Pipeline callback fired | task_name={call_back_pipeline['task_name']}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logging.info(f"📸 Pipeline callback already fired (dedup OK) | task_name={call_back_pipeline['task_name']}")
        else:
            logging.warning(f"📸 Pipeline callback error (non-fatal) | {e}")


def create_video_keyframes_task(
    cloud_task_client: CloudTasks,
    queue_name: str,
    payload: dict,
    task_name: str,
    timeout_seconds: int = CLOUD_TASK_TIMEOUT_SECONDS,
):
    task = CloudTask(
        queue_name=queue_name,
        name=task_name,
        http_method="POST",
        url=CLOUD_TASK_HANDLER_URL,
        payload=payload,
        include_oidc_token=True,
        timeout=timeout_seconds,
    )
    cloud_task_client.add_task(task)

    logging.info(
        f"✅ Cloud Task créée | task_name={task_name} | "
        f"queue_name={queue_name} | timeout_seconds={timeout_seconds}"
    )


def is_near_cloud_task_timeout(start_time_monotonic: float, margin_seconds: int) -> bool:
    elapsed_seconds = time.monotonic() - start_time_monotonic
    remaining_time_seconds = CLOUD_TASK_TIMEOUT_SECONDS - elapsed_seconds
    return remaining_time_seconds <= margin_seconds


def serialize_dates(obj):
    if isinstance(obj, dict):
        return {k: serialize_dates(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_dates(v) for v in obj]
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    return obj
