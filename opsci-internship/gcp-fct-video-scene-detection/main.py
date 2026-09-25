import os
import time
import psutil
import logging
import traceback
import datetime
import shutil

from flask import Request
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Optional
from google.cloud import storage

from gcpOpsci.tools import log_and_return_response, make_task_id, PerfTimer
from gcpOpsci.cloud_tasks import CloudTasks, CloudTask
from gcpOpsci.tools.utils_models import OpsciDefaultRequest

from utils.video_scene import detect_scenes
from utils.crud_documents import (
    get_documents_by_ids,
    get_video_documents,
    bulk_update_documents,
    persist_scene_detection_batch,
    repopulate_scene_chunks_from_existing,
)
from utils.gcs import download_gcs_url_to_tmp, cleanup_local_file


logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


CLOUD_TASK_HANDLER_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-scene-detection"
)
TRANSCRIPTION_CF_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-transcription"
)
TRANSCRIPTION_QUEUE_NAME = "video-transcription"
KEYFRAMES_CF_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-keyframes-selection"
)
KEYFRAMES_QUEUE_NAME = "video-keyframes-selection"

CLOUD_TASK_TIMEOUT_SECONDS = 30 * 60
CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS = 10 * 60


class RequestParams(BaseModel):
    blob_urls: Optional[list[str]] = Field(
        None,
        description="Liste d'URLs GCS des vidéos à traiter (mode direct, sans BDD)",
    )

    importation_ids: Optional[list[int]] = Field(None, description="Liste d'IDs d'importations. Accepte aussi un int ou une string CSV ('22,696').")
    documents_ids: Optional[list[int]] = Field(
        None,
        description="Liste d'IDs de documents à traiter",
    )

    force_all: Optional[bool] = Field(
        False,
        description="Forcer le retraitement même si la détection de scènes existe déjà",
    )

    start_date: Optional[datetime.datetime] = Field(
        None,
        description="Date de création des posts (début)",
    )
    end_date: Optional[datetime.datetime] = Field(
        None,
        description="Date de création des posts (fin)",
    )
    inclure_original: Optional[bool] = Field(True, description="Inclure les posts de type 'original'")
    inclure_comments: Optional[bool] = Field(True, description="Inclure les posts de type 'comment'")
    inclure_retweets: Optional[bool] = Field(True, description="Inclure les repartages")
    inclure_citations: Optional[bool] = Field(True, description="Inclure les posts cités")

    min_scene_duration: Optional[float] = Field(
        2.0,
        description="Durée minimale d'une scène en secondes",
    )
    visual_disctinct_threshold: Optional[float] = Field(
        30.0,
        description="Seuil de sensibilité du détecteur de scènes (ContentDetector)",
    )

    scenes_analyse_id: int = Field(
        ...,
        description="Identifiant de l'analyse visuelle (scènes)",
    )
    keyframes_analyse_id: Optional[int] = Field(
        None,
        description="Identifiant de l'analyse des keyframes vidéo. Requis si auto_select_keyframes=True.",
    )

    auto_transcribe: Optional[bool] = Field(
        False,
        description="Déclencher automatiquement video-transcription après détection des scènes",
    )

    auto_select_keyframes: Optional[bool] = Field(
        False,
        description="Déclencher automatiquement video-keyframes-selection directement après détection des scènes (sans passer par la transcription).",
    )

    ## Params pass-through pour video-keyframes-selection (utilisés si auto_select_keyframes=True)
    visually_distinct_frame_threshold: Optional[float] = Field(None, description="Seuil Bhattacharyya pour la sélection des keyframes.")
    target_fps: Optional[float] = Field(None, description="FPS cible pour video-keyframes-selection.")
    max_keyframes_for_scene_embeddings: Optional[int] = Field(None, description="Cap de candidats par scène.")
    max_keyframes_per_video: Optional[int] = Field(None, description="Cap de keyframes par vidéo.")
    max_frames_to_decode: Optional[int] = Field(None, description="Frames max à décoder par scène.")
    clip_model_name: Optional[str] = Field(None, description="Modèle CLIP OpenCLIP.")
    clip_pretrained: Optional[str] = Field(None, description="Checkpoint pretrained OpenCLIP.")
    device: Optional[str] = Field(None, description="Device CLIP (cpu/cuda/auto).")
    keyframes_bucket: Optional[str] = Field(None, description="Bucket GCS pour les keyframes.")

    batch_size: Optional[int] = Field(
        64,
        description="Taille des batches pour le découpage en tâches Cloud Tasks",
    )
    parent_job_id: Optional[str] = Field(None, description="Identifiant du job parent")
    queue_name: Optional[str] = Field(
        "video-scene-detection",
        description="Nom de la file Cloud Tasks",
    )
    call_back_pipeline: Optional[dict] = Field(
        None,
        description="Paramètres du callback vers pipeline-topic-modeling à déclencher par la CF feuille en fin de job.",
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


@log_and_return_response
def run(request: Request):
    db = None

    try:
        my_request = OpsciDefaultRequest(request, RequestParams)

        db = my_request.db
        params: RequestParams = my_request.params
        storage_client = storage.Client()
        cloud_task_client = CloudTasks()

        logging.info(f"🎬 Nouvelle requête avec les paramètres : {params}")

        ## Fire-and-forget
        if params.parent_job_id is None:
            task_payload = serialize_dates(params.model_dump(exclude_unset=True))
            job_id = make_task_id(task_payload)
            task_payload["parent_job_id"] = job_id

            logging.info(
                f"🎬 Appel initial détecté. Dispatching job {job_id} vers Cloud Tasks..."
            )

            task_name = f"a{params.scenes_analyse_id}-j{job_id}-init"
            _create_task(
                cloud_task_client=cloud_task_client,
                url=CLOUD_TASK_HANDLER_URL,
                queue_name=params.queue_name,
                payload=task_payload,
                task_name=task_name,
            )

            return {
                "status": "accepted",
                "job_id": job_id,
                "message": "Job dispatché vers Cloud Tasks. Utilisez job_id pour suivre la progression.",
            }, 202

        job_id = params.parent_job_id

        retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", 0))
        timer = PerfTimer(function_name="video-scene-detection", parent_job_id=params.parent_job_id, retry_count=retry_count)
        worker_start_time_monotonic = time.monotonic()

        logging.info(f"🎬 Démarrage du job_id : {job_id}")
        _log_memory("start_job")

        start_date = params.start_date.isoformat() if params.start_date else None
        end_date = params.end_date.isoformat() if params.end_date else None

        #######################################################################
        # SCENARIO 1 : blob_urls → détection directe, retourne JSON
        #######################################################################
        if params.blob_urls:
            logging.info(f"🎬 Scénario 1 : Détection directe de {len(params.blob_urls)} vidéo(s)")

            results = {}
            errors = []

            for gs_url in set(params.blob_urls):
                local_path = None
                logging.info(f"🎬 START blob_url={gs_url}")

                try:
                    local_path = download_gcs_url_to_tmp(
                        gs_url=gs_url,
                        storage_client=storage_client,
                    )

                    scenes, video_metadata = detect_scenes(
                        video_path=local_path,
                        min_scene_duration=params.min_scene_duration,
                        threshold=params.visual_disctinct_threshold,
                    )
                    logging.info(f"🎬 {len(scenes)} scènes détectées | blob_url={gs_url}")

                    results[gs_url] = {
                        "video_metadata": {
                            "gcs_uri": gs_url,
                            "fps": video_metadata.get("fps"),
                            "frame_count": video_metadata.get("frame_count"),
                            "duration_seconds": video_metadata.get("duration"),
                            "width": video_metadata.get("width"),
                            "height": video_metadata.get("height"),
                        },
                        "scene_transcription": {
                            "status": "done" if scenes else "failed",
                            "scene_count": len(scenes),
                            "scenes": [
                                {
                                    "index": scene["index"],
                                    "start_frame": scene["start_frame"],
                                    "end_frame": scene["end_frame"],
                                    "start_ms": scene["start_ms"],
                                    "end_ms": scene["end_ms"],
                                    "transcript": "",
                                }
                                for scene in scenes
                            ],
                        },
                    }

                except Exception as e:
                    error_msg = {"blob_url": gs_url, "error": f"🎬 {str(e)}"}
                    logging.error(error_msg)
                    errors.append(error_msg)

                finally:
                    if local_path:
                        cleanup_local_file(local_path)

            timer.n_items_read = len(params.blob_urls)
            timer.n_items_written = len(results)
            timer.emit()
            return {
                "job_id": job_id,
                "results": results,
                "processed_count": len(results),
                "failed_count": len(errors),
            }, 200

        #######################################################################
        # SCENARIO 2 : documents_ids fournis
        # SCENARIO 3 : importation_id + filtres
        #######################################################################
        if params.documents_ids:
            logging.info(
                f"🎬 Mode Worker : {len(params.documents_ids)} documents spécifiques."
            )
            docs_data = get_documents_by_ids(db, params.documents_ids)
            # get_documents_by_ids retourne (importation_id, id, blob_url)

        else:
            if not params.importation_ids:
                return "Missing importation_ids or documents_ids", 400

            logging.info(
                f"🎬 Recherche des vidéos dans les importations {params.importation_ids}..."
            )

            docs_data = get_video_documents(
                db,
                importation_ids=params.importation_ids,
                start_date=start_date,
                end_date=end_date,
                include_original=params.inclure_original,
                include_quotes=params.inclure_citations,
                include_shares=params.inclure_retweets,
                include_comments=params.inclure_comments,
                include_scene_detected=params.force_all,
            )

            # Vidéos déjà détectées (exclues ci-dessus) mais absentes du nouveau scenes_analyse_id :
            # on les repopule depuis les documents scène existants sans re-télécharger les vidéos.
            if not params.force_all:
                n_repopulated = repopulate_scene_chunks_from_existing(
                    db,
                    importation_ids=params.importation_ids,
                    scenes_analyse_id=params.scenes_analyse_id,
                )
                if n_repopulated > 0:
                    logging.info(
                        f"🎬 {n_repopulated} chunks de scènes repopulés depuis données existantes "
                        f"(scenes_analyse_id={params.scenes_analyse_id}, importation_ids={params.importation_ids})."
                    )

        if not docs_data:
            # Scènes déjà détectées (force_all=False). Si auto_select_keyframes=True,
            # déléguer quand même à video-keyframes-selection par importation_id :
            # il trouvera les docs qui ont des scènes mais pas encore de keyframes.
            # video-keyframes-selection prend en charge le callback en fin de job.
            if params.auto_select_keyframes and params.importation_ids:
                _trigger_keyframes_selection(
                    cloud_task_client=cloud_task_client,
                    job_id=job_id,
                    document_ids=None,
                    importation_ids=params.importation_ids,
                    params=params,
                    suffix="fromsd",
                )
            elif params.auto_transcribe and params.importation_ids:
                # La transcription prendra en charge le callback
                _trigger_transcription(
                    cloud_task_client=cloud_task_client,
                    job_id=job_id,
                    params=params,
                    importation_ids=params.importation_ids,
                    suffix="fromsd",
                )
            elif params.call_back_pipeline:
                # Aucun downstream déclenché → cette CF doit tirer le callback elle-même
                _fire_pipeline_callback(params.call_back_pipeline)
            timer.n_items_read = 0
            timer.emit()
            return {
                "message": "Aucun document vidéo trouvé pour ces critères.",
                "processed_count": 0,
                "job_id": job_id,
            }, 200

        docs_ids_to_process = [doc_id for _, doc_id, _ in docs_data]

        #######################################################################
        # DISPATCH CLOUD TASKS SI BESOIN
        #######################################################################
        task_batch_limit = params.batch_size or 128

        if len(docs_ids_to_process) > task_batch_limit:
            logging.info(
                f"🎬 Dispatching {len(docs_ids_to_process)} documents en batches de {task_batch_limit}"
            )

            batches = [
                docs_ids_to_process[i: i + task_batch_limit]
                for i in range(0, len(docs_ids_to_process), task_batch_limit)
            ]

            base_payload = serialize_dates(params.model_dump(exclude_unset=True))
            base_payload.pop("importation_id", None)
            base_payload.pop("blob_urls", None)
            base_payload["parent_job_id"] = job_id

            task_ids_log = []
            for batch_ids in batches:
                task_payload = {**base_payload, "documents_ids": batch_ids}
                t_id = make_task_id(task_payload)
                task_ids_log.append(t_id)
                _create_task(
                    cloud_task_client=cloud_task_client,
                    url=CLOUD_TASK_HANDLER_URL,
                    queue_name=params.queue_name,
                    payload=task_payload,
                    task_name=f"a{params.scenes_analyse_id}-j{job_id}-t{t_id}",
                )

            timer.n_items_read = len(docs_ids_to_process)
            timer.n_tasks_dispatched = len(task_ids_log)
            timer.emit({"dispatch_type": "batch_split"})
            return {
                "message": f"Dispatch terminé. {len(task_ids_log)} tâches créées pour {len(docs_ids_to_process)} vidéos.",
                "job_id": job_id,
            }, 200

        #######################################################################
        # EXECUTION DIRECTE / WORKER
        #######################################################################
        logging.info(f"🎬 Exécution pour {len(docs_data)} vidéos...")

        errors = []
        processed_count = 0
        processed_ids: list[int] = []
        video_scenes_batch: list[tuple[int, int, list]] = []
        docs_to_update: list[dict] = []

        for current_index, (importation_id, document_id, blob_url) in enumerate(docs_data):
            local_path = None

            logging.info(f"🎬 START document_id={document_id} | blob_url={blob_url}")

            try:
                local_path = download_gcs_url_to_tmp(
                    gs_url=blob_url,
                    storage_client=storage_client,
                )

                scenes, video_metadata = detect_scenes(
                    video_path=local_path,
                    min_scene_duration=params.min_scene_duration,
                    threshold=params.visual_disctinct_threshold,
                )
                logging.info(f"🎬 {len(scenes)} scènes | document_id={document_id}")

                video_scenes_batch.append((document_id, importation_id, scenes))

                docs_to_update.append({
                    "id": document_id,
                    "json_data": {
                        "scenes_metadata": {
                            "fps": video_metadata.get("fps"),
                            "frame_count": video_metadata.get("frame_count"),
                            "duration_seconds": video_metadata.get("duration"),
                            "width": video_metadata.get("width"),
                            "height": video_metadata.get("height"),
                            "scene_count": len(scenes),
                        },
                    },
                })

                processed_count += 1
                processed_ids.append(document_id)

            except Exception as e:
                error_data = {
                    "document_id": document_id,
                    "blob_url": blob_url,
                    "error": f"🎬 {str(e)}",
                }
                logging.error(error_data)
                errors.append(error_data)

            finally:
                if local_path:
                    cleanup_local_file(local_path)

                if _is_near_timeout(worker_start_time_monotonic):
                    persist_scene_detection_batch(
                        db=db,
                        video_scenes_batch=video_scenes_batch,
                        analyse_id=params.scenes_analyse_id,
                    )
                    bulk_update_documents(db=db, documents_to_update=docs_to_update)
                    video_scenes_batch = []
                    docs_to_update = []

                    if params.auto_transcribe and params.importation_ids:
                        _trigger_transcription(
                            cloud_task_client=cloud_task_client,
                            job_id=job_id,
                            params=params,
                            importation_ids=params.importation_ids,
                            suffix="timeout",
                        )
                    if params.auto_select_keyframes and params.importation_ids:
                        _trigger_keyframes_selection(
                            cloud_task_client=cloud_task_client,
                            job_id=job_id,
                            importation_ids=params.importation_ids,
                            params=params,
                            suffix="timeout",
                        )
                    processed_ids = []

                    remaining_docs = docs_data[current_index + 1:]
                    if remaining_docs:
                        remaining_ids = [doc_id for _, doc_id, _ in remaining_docs]
                        next_payload = serialize_dates(params.model_dump(exclude_unset=True))
                        next_payload.pop("importation_id", None)
                        next_payload.pop("blob_urls", None)
                        next_payload["parent_job_id"] = job_id
                        next_payload["documents_ids"] = remaining_ids

                        next_task_id = make_task_id(next_payload)
                        _create_task(
                            cloud_task_client=cloud_task_client,
                            url=CLOUD_TASK_HANDLER_URL,
                            queue_name=params.queue_name,
                            payload=next_payload,
                            task_name=f"a{params.scenes_analyse_id}-j{job_id}-resume-{next_task_id}",
                        )

                        logging.warning(
                            f"⏳ Timeout proche. Relance planifiée avec "
                            f"{len(remaining_ids)} documents restants."
                        )

                    if not remaining_docs and params.call_back_pipeline:
                        if not (params.auto_transcribe and params.importation_ids) and not (params.auto_select_keyframes and params.importation_ids):
                            _fire_pipeline_callback(params.call_back_pipeline)

                    timer.n_items_read = len(docs_data)
                    timer.n_items_written = processed_count
                    timer.emit({"partial_timeout": True, "remaining_count": len(remaining_docs)})
                    return {
                        "message": "Succès partiel avant timeout. Reprise planifiée.",
                        "processed_count": processed_count,
                        "failed_count": len(errors),
                        "remaining_count": len(remaining_docs),
                        "job_id": job_id,
                    }, 200

        persist_scene_detection_batch(
            db=db,
            video_scenes_batch=video_scenes_batch,
            analyse_id=params.scenes_analyse_id,
        )
        bulk_update_documents(db=db, documents_to_update=docs_to_update)

        if params.auto_transcribe and params.importation_ids:
            _trigger_transcription(
                cloud_task_client=cloud_task_client,
                job_id=job_id,
                params=params,
                importation_ids=params.importation_ids,
                suffix="final",
            )
        if params.auto_select_keyframes and params.importation_ids:
            _trigger_keyframes_selection(
                cloud_task_client=cloud_task_client,
                job_id=job_id,
                importation_ids=params.importation_ids,
                params=params,
                suffix="final",
            )

        timer.n_items_read = len(docs_data)
        timer.n_items_written = processed_count
        timer.emit()
        return {
            "message": "Succès",
            "processed_count": processed_count,
            "failed_count": len(errors),
            "job_id": job_id,
        }, 200

    except Exception as e:
        logging.error(f"🎬 Critical Error: {e}")
        logging.error(traceback.format_exc())
        if db:
            db.rollback()
        return f"Error: {e}", 500

    finally:
        if db:
            db.close()
        _log_memory("end_of_task")


def _trigger_transcription(
    cloud_task_client: CloudTasks,
    job_id: str,
    params,
    importation_ids: list[int] | None = None,
    document_ids: list[int] | None = None,
    suffix: str = "",
):
    """Crée une tâche video-transcription.

    Accepte soit importation_ids (video-transcription interroge la BDD avec
    include_transcribed=False pour ne pas re-transcrire) soit document_ids
    (liste explicite — scope restreint au batch courant).
    Préférer importation_ids pour couvrir tous les docs avec scènes sans transcription.
    """
    payload: dict = {"parent_job_id": job_id}
    if importation_ids is not None:
        payload["importation_ids"] = importation_ids
    elif document_ids is not None:
        payload["documents_ids"] = document_ids
    ## Filtrage temporel et inclusion
    for key in [
        "start_date", "end_date",
        "inclure_original", "inclure_comments", "inclure_retweets", "inclure_citations",
    ]:
        val = getattr(params, key, None)
        if val is not None:
            payload[key] = serialize_dates({key: val})[key]
    ## Pass-through du callback pipeline
    if params.call_back_pipeline is not None:
        payload["call_back_pipeline"] = params.call_back_pipeline

    task_id = make_task_id(payload)
    task_name = f"j{job_id}-transcription-{suffix}-{task_id}"
    _create_task(
        cloud_task_client=cloud_task_client,
        url=TRANSCRIPTION_CF_URL,
        queue_name=TRANSCRIPTION_QUEUE_NAME,
        payload=payload,
        task_name=task_name,
    )
    doc_count = len(document_ids) if document_ids is not None else f"importation_ids={importation_ids}"
    logging.info(
        f"🎬 Tâche de transcription créée | task_name={task_name} | "
        f"document_count={doc_count}"
    )


def _trigger_keyframes_selection(
    cloud_task_client: CloudTasks,
    job_id: str,
    params,
    document_ids: list[int] | None = None,
    importation_ids: list[int] | None = None,
    suffix: str = "",
):
    """Crée une tâche video-keyframes-selection.

    Accepte soit document_ids (liste explicite de documents à traiter)
    soit importation_ids (la CF cible interroge elle-même la BDD avec include_keyframed=False).
    """
    payload: dict = {
        "scenes_analyse_id": params.scenes_analyse_id,
        "keyframes_analyse_id": params.keyframes_analyse_id,
        "parent_job_id": job_id,
    }
    if document_ids is not None:
        payload["documents_ids"] = document_ids
    elif importation_ids is not None:
        payload["importation_ids"] = importation_ids
    ## Pass-through des params de filtrage temporel et d'inclusion
    for key in [
        "start_date", "end_date",
        "inclure_original", "inclure_comments", "inclure_retweets", "inclure_citations",
    ]:
        val = getattr(params, key, None)
        if val is not None:
            payload[key] = serialize_dates({key: val})[key]
    ## Pass-through des params keyframes optionnels (None = CF utilise ses propres defaults)
    for key in [
        "visually_distinct_frame_threshold", "target_fps", "max_keyframes_for_scene_embeddings",
        "max_keyframes_per_video", "max_frames_to_decode", "clip_model_name",
        "clip_pretrained", "device", "keyframes_bucket",
    ]:
        val = getattr(params, key, None)
        if val is not None:
            payload[key] = val
    ## Pass-through du callback pipeline
    if params.call_back_pipeline is not None:
        payload["call_back_pipeline"] = params.call_back_pipeline

    task_id = make_task_id(payload)
    kf_analyse_id = params.keyframes_analyse_id
    task_name = f"a{kf_analyse_id}-j{job_id}-keyframes-{suffix}-{task_id}"
    _create_task(
        cloud_task_client=cloud_task_client,
        url=KEYFRAMES_CF_URL,
        queue_name=KEYFRAMES_QUEUE_NAME,
        payload=payload,
        task_name=task_name,
    )
    doc_count = len(document_ids) if document_ids is not None else f"importation_ids={importation_ids}"
    logging.info(
        f"🎬 Tâche keyframes-selection créée | task_name={task_name} | "
        f"document_count={doc_count}"
    )


def _fire_pipeline_callback(call_back_pipeline: dict) -> None:
    """Crée la Cloud Task de callback vers pipeline-topic-modeling.

    Utilisé par cette CF uniquement quand aucun downstream n'est déclenché
    (0 docs + auto_select_keyframes=False + auto_transcribe=False).
    Dans les autres cas, c'est la CF feuille (keyframes-selection ou transcription)
    qui tire le callback.
    """
    try:
        cloud_task_client = CloudTasks()
        task = CloudTask(
            queue_name=call_back_pipeline["queue_name"],
            name=call_back_pipeline["task_name"],
            http_method="POST",
            url=call_back_pipeline["url"],
            payload=call_back_pipeline["payload"],
            include_oidc_token=True,
            timeout=1800,
        )
        cloud_task_client.add_task(task)
        logging.info(f"🎬 Pipeline callback fired | task_name={call_back_pipeline['task_name']}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logging.info(f"🎬 Pipeline callback already fired (dedup OK) | task_name={call_back_pipeline['task_name']}")
        else:
            logging.warning(f"🎬 Pipeline callback error (non-fatal) | {e}")


def _create_task(
    cloud_task_client: CloudTasks,
    url: str,
    queue_name: str,
    payload: dict,
    task_name: str,
):
    task = CloudTask(
        queue_name=queue_name,
        name=task_name,
        http_method="POST",
        url=url,
        payload=payload,
        include_oidc_token=True,
        timeout=CLOUD_TASK_TIMEOUT_SECONDS,
    )
    cloud_task_client.add_task(task)
    logging.info(f"✅ Cloud Task créée | task_name={task_name} | queue_name={queue_name}")


def _is_near_timeout(start_time_monotonic: float) -> bool:
    elapsed = time.monotonic() - start_time_monotonic
    return (CLOUD_TASK_TIMEOUT_SECONDS - elapsed) <= CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS


def _log_memory(stage: str):
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / 1024 / 1024
        _, tmp_used, tmp_free = shutil.disk_usage("/tmp")
        logging.info(
            f"🧠 MEMORY | stage={stage} | "
            f"process_rss_mb={rss_mb:.2f} | "
            f"/tmp_used_mb={tmp_used / 1024 / 1024:.2f} | "
            f"/tmp_free_mb={tmp_free / 1024 / 1024:.2f}"
        )
    except Exception as e:
        logging.warning(f"🧠 MEMORY LOG FAILED | stage={stage} | error={e}")


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
