import gc
import os
import time
import psutil
import logging
import traceback
import datetime
import shutil

from flask import Request
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Optional, Literal

import torch
import whisperx
from google.cloud import storage

from gcpOpsci.tools import log_and_return_response, make_task_id, PerfTimer
from gcpOpsci.cloud_tasks import CloudTasks, CloudTask
from gcpOpsci.tools.utils_models import OpsciDefaultRequest

from utils.transcription import (
    Scene,
    transcribe_video,
    add_scenes_transcripts,
    get_media_duration_ms,
)
from utils.cache import ensure_memory_headroom_for_alignment_cache
from utils.crud_documents import (
    VIDEO_EXTENSIONS,
    get_documents_by_ids,
    get_video_documents,
    get_scene_documents_by_video_ids,
    bulk_update_documents,
)
from utils.gcs import download_gcs_url_to_tmp, cleanup_local_file


logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


CLOUD_TASK_HANDLER_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-transcription"
)
CLOUD_TASK_TIMEOUT_SECONDS = 30 * 60
CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS = 10 * 60



class RequestParams(BaseModel):
    blob_urls: Optional[list[str]] = Field(
        None,
        description="Liste d'URLs GCS des videos à transcrire",
    )

    importation_ids: Optional[list[int]] = Field(None, description="Liste d'IDs d'importations. Accepte aussi un int ou une string CSV ('22,696').")
    documents_ids: Optional[list[int]] = Field(
        None,
        description="Liste d'IDs de documents à traiter",
    )

    force_all: Optional[bool] = Field(
        False,
        description="Forcer le retraitement même si les transcriptions existent déjà",
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

    whisperx_model: Optional[str] = Field(
        "turbo",
        description="La taille du modèle de transcription",
    )
    device: Optional[Literal["cpu", "cuda", "auto"]] = Field(
        "auto",
        description="Le device de transcription",
    )
    transcription_language: Optional[str] = Field(
        None,
        description="Langue forcée pour WhisperX",
    )

    priority_languages: Optional[list[str]] = Field(
        default_factory=list,
        description="Langues prioritaires à garder en cache pour l'alignement",
    )

    transcribe_only_video: Optional[bool] = Field(
        False,
        description="Si True, ignore les fichiers non-vidéo. Formats acceptés : .mp4, .avi, .mov, .mkv, .webm (formats OpenCV). Mettre False pour traiter aussi les fichiers audio.",
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
        "video-transcription",
        description="Nom de la file Cloud Tasks",
    )
    call_back_pipeline: Optional[dict] = Field(
        None,
        description="Callback vers pipeline-topic-modeling à déclencher par la dernière tâche du job.",
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


WHISPERX_MODEL = None
WHISPERX_MODEL_NAME = None
WHISPERX_MODEL_DEVICE = None


#---- Chargement paresseux du modèle WhisperX, appelé UNIQUEMENT par les chemins qui transcrivent
## réellement (scénario 1 `blob_urls`, et boucle worker). Le corps est celui qui vivait dans `run()`
## avant le 2026-07-28, déplacé tel quel : mêmes logs, même `timer.model_load()`, même cache
## d'instance via les trois globales. Seul le MOMENT de l'appel change.
def _ensure_whisperx_model(model_name: str, device: str, timer: PerfTimer):
    """Charge WhisperX si besoin, le renvoie avec l'indicateur de réutilisation du cache."""
    global WHISPERX_MODEL, WHISPERX_MODEL_NAME, WHISPERX_MODEL_DEVICE

    model_was_cached = (
        WHISPERX_MODEL is not None
        and WHISPERX_MODEL_NAME == model_name
        and WHISPERX_MODEL_DEVICE == device
    )

    should_reload_model = (
        WHISPERX_MODEL is None
        or WHISPERX_MODEL_NAME != model_name
        or WHISPERX_MODEL_DEVICE != device
    )

    if should_reload_model:
        if WHISPERX_MODEL is not None:
            logging.info(
                f"🧹 Releasing previous WhisperX model | "
                f"old_model={WHISPERX_MODEL_NAME} | old_device={WHISPERX_MODEL_DEVICE}"
            )
            try:
                old_model = WHISPERX_MODEL
                WHISPERX_MODEL = None
                del old_model
            except Exception:
                pass

            gc.collect()
            if device == "cuda":
                torch.cuda.empty_cache()

        logging.info(
            f"🎬 Chargement du modèle WhisperX | model={model_name} | device={device}"
        )

        with timer.model_load():
            WHISPERX_MODEL = whisperx.load_model(
                model_name,
                device,
                compute_type="float16" if device == "cuda" else "int8",
            )
        WHISPERX_MODEL_NAME = model_name
        WHISPERX_MODEL_DEVICE = device

        log_memory("after_whisperx_model_load")

    logging.info(f"🎬 Model loaded : WhisperX-{model_name}")

    return WHISPERX_MODEL, model_was_cached
#----


@log_and_return_response
def run(request: Request):
    db = None

    try:
        worker_start_time_monotonic = time.monotonic()

        my_request = OpsciDefaultRequest(request, RequestParams)

        db = my_request.db
        params: RequestParams = my_request.params
        storage_client = storage.Client()
        cloud_task_client = CloudTasks()

        logging.info(f"Nouvelle requête avec les paramètres : {params}")

        # Job ID
        if params.parent_job_id is None:
            task_payload = serialize_dates(params.model_dump(exclude_unset=True))
            job_id = make_task_id(task_payload)
            task_payload["parent_job_id"] = job_id

            logging.info(
                f"🎬 Appel initial détecté. Dispatching job {job_id} vers Cloud Tasks..."
            )

            task_name = f"j{job_id}-video-init"

            create_video_transcription_task(
                cloud_task_client=cloud_task_client,
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
        timer = PerfTimer(function_name="video-transcription", parent_job_id=job_id, retry_count=retry_count)
        gpu_allocated_mb = 0
        gpu_peak_mb = 0

        logging.info(f"Démarrage du job_id : {job_id}")
        log_memory("start_job")

        start_date = params.start_date.isoformat() if params.start_date else None
        end_date = params.end_date.isoformat() if params.end_date else None

        if params.device == "auto" or params.device is None:
            params.device = "cuda" if torch.cuda.is_available() else "cpu"

        #---- Le modèle n'est PLUS chargé ici. Cette CF a deux rôles disjoints : DISPATCHER
        ## (découper les fichiers en tâches Cloud Tasks puis rendre 200 sans rien transcrire) et
        ## TRANSCRIRE. Charger WhisperX avant le dispatch le faisait donc payer intégralement par
        ## une tâche qui n'en fait aucun usage. Mesuré le 2026-07-28, job FoImxB4zORqs (analyse 628) :
        ##   17:40:46.400  🎬 Chargement du modèle WhisperX | model=turbo | device=cuda
        ##   17:41:19.951  🧠 MEMORY | after_whisperx_model_load | rss=1728 MB | /tmp_used=1547 MB
        ##   17:41:20.393  🎬 Dispatching vers Cloud Tasks : 257 fichier(s) vidéo (> 64)
        ##   17:41:20.753  [HTTP POST / : 200, 35 s] Dispatch terminé. 5 tâches créées
        ## soit 33,5 s des 35,7 s de la tâche passés à charger un modèle jamais utilisé, plus 1,5 GB
        ## de /tmp (= RAM sur Cloud Run) et ~1,1 GB de RSS immobilisés sur une instance GPU.
        ## L'appel est désormais fait au plus tard, juste avant chaque usage réel : scénario 1
        ## (`blob_urls`) et boucle worker. Les chemins qui répondent sans transcrire — dispatch,
        ## aucun document trouvé, 400 — ne le chargent plus du tout.
        ## `model_was_cached` vaut False sur ces chemins ; dans le même événement [PERF],
        ## `model_load_ms=0` les distingue sans ambiguïté d'un vrai chargement.
        ## La résolution de `params.device` reste AVANT ce point : `base_payload` la propage aux
        ## tâches filles (`serialize_dates(params.model_dump(...))`), qui ne doivent pas re-résoudre
        ## "auto" sur une instance dont le GPU serait indisponible.
        model_was_cached = False
        #----

        #######################################################################
        # SCENARIO 1 : blob_urls fourni -> transcription directe -> retourne JSON
        #######################################################################
        if params.blob_urls:

            blob_urls_to_process = set(params.blob_urls)
            if params.transcribe_only_video:
                blob_urls_to_process = {
                    url for url in blob_urls_to_process
                    if os.path.splitext(url.lower())[1] in VIDEO_EXTENSIONS
                }
                skipped = len(params.blob_urls) - len(blob_urls_to_process)
                if skipped:
                    logging.info(f"🎬 Scénario 1 : {skipped} URL(s) ignorée(s) (non-vidéo)")

            logging.info(f"🎬 Scénario 1 : Transcription directe de {len(blob_urls_to_process)} fichier(s)")

            whisperx_model, model_was_cached = _ensure_whisperx_model(
                params.whisperx_model, params.device, timer
            )

            results = {}
            errors = []

            for gs_url in blob_urls_to_process:
                local_path = None

                logging.info(f"🎬 START blob_url={gs_url}")

                ensure_memory_headroom_for_alignment_cache(
                    priority_languages=params.priority_languages or [],
                    device=params.device,
                )

                try:
                    local_path = download_gcs_url_to_tmp(
                        gs_url=gs_url,
                        storage_client=storage_client,
                    )

                    duration_ms = get_media_duration_ms(local_path)
                    scenes = [
                        Scene(
                            index=0,
                            start_frame=0,
                            end_frame=-1,
                            start_ms=0,
                            end_ms=duration_ms,
                            transcript="",
                        )
                    ]

                    with timer.inference():
                        transcript_segments, detected_language = transcribe_video(
                            video_path=local_path,
                            model=whisperx_model,
                            device=params.device,
                            language=params.transcription_language,
                        )

                    if params.device == "cuda":
                        gpu_allocated_mb = torch.cuda.memory_allocated() // 1024**2
                        gpu_peak_mb = torch.cuda.max_memory_allocated() // 1024**2
                    else:
                        gpu_allocated_mb = 0
                        gpu_peak_mb = 0

                    scenes = add_scenes_transcripts(scenes, transcript_segments)

                    full_transcript = " ".join(
                        scene["transcript"].strip()
                        for scene in scenes
                        if scene["transcript"] is not None and scene["transcript"].strip()
                    ) or None

                    results[gs_url] = {
                        "text": full_transcript,
                        "lang": detected_language,
                        "video_metadata": {
                            "gcs_uri": gs_url,
                            "duration_seconds": duration_ms / 1000,
                        },
                        "scene_transcription": {
                            "status": "done",
                            "language": detected_language,
                            "scene_count": len(scenes),
                            "scenes": [
                                {
                                    "index": scene["index"],
                                    "start_frame": scene["start_frame"],
                                    "end_frame": scene["end_frame"],
                                    "start_ms": scene["start_ms"],
                                    "end_ms": scene["end_ms"],
                                    "transcript": scene["transcript"],
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

            timer.n_items_written = len(results)
            timer.emit({"model_was_cached": model_was_cached, "gpu_memory_allocated_mb": gpu_allocated_mb, "gpu_memory_peak_mb": gpu_peak_mb, "scenario": "blob_urls"})
            return {
                "job_id": job_id,
                "results": results,
                "processed_count": len(results),
                "failed_count": len(errors),
            }, 200

        #######################################################################
        # SCENARIO 2 : BDD avec documents_ids
        # SCENARIO 3 : BDD avec importation_id + filtres
        #######################################################################
        if params.documents_ids:
            logging.info(
                f"🎬 Mode Worker : Traitements de {len(params.documents_ids)} documents spécifiques."
            )
            with timer.sql_read():
                docs_data = get_documents_by_ids(db, params.documents_ids)
            timer.n_items_read = len(docs_data)

        else:
            if not params.importation_ids:
                timer.emit({"error": "Missing importation_ids or documents_ids"})
                return "Missing importation_ids or documents_ids", 400

            logging.info(
                f"🎬 Recherche des vidéos à traiter dans les importations {params.importation_ids}..."
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
                    include_transcribed=params.force_all,
                    video_only=params.transcribe_only_video,
                )
            timer.n_items_read = len(docs_data)

        if not docs_data:
            if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
                _fire_pipeline_callback(params.call_back_pipeline)
            timer.emit({"model_was_cached": model_was_cached, "scenario": "no_docs"})
            return {
                "message": "Aucun document vidéo trouvé pour ces critères.",
                "processed_count": 0,
                "job_id": job_id,
            }, 200

        docs_ids_to_process = [doc_id for doc_id, *_ in docs_data]

        #---- Regroupement par blob_url : UN fichier vidéo est souvent référencé par PLUSIEURS
        ## documents. Une vidéo repartagée ou citée produit un document par post, tous avec le même
        ## `blob_url`. La sélection SQL renvoie une ligne par DOCUMENT (`SELECT DISTINCT
        ## v.document_id`), donc sans ce regroupement WhisperX tournait une fois par document.
        ## Mesuré le 2026-07-28 sur l'importation 1201 : 616 documents pour 268 blob_url distincts,
        ## soit 2,3 inférences GPU par vidéo réelle. Même motif que `document-downloader`, qui groupe
        ## déjà par `document_url` (array_agg) et propage le résultat sur tous les document_id.
        ## `dict` conserve l'ordre d'insertion : l'ordre de traitement reste celui de la requête,
        ## donc la progression et la reprise après timeout restent déterministes.
        docs_by_blob: dict[str, list[tuple[int, Any]]] = {}
        for _doc_id, _blob_url, _json_data in docs_data:
            docs_by_blob.setdefault(_blob_url, []).append((_doc_id, _json_data))
        grouped_items = list(docs_by_blob.items())
        logging.info(
            f"🎬 {len(docs_ids_to_process)} document(s) → {len(grouped_items)} fichier(s) distinct(s) "
            f"à transcrire (économie : {len(docs_ids_to_process) - len(grouped_items)} inférence(s))"
        )
        #----

        #######################################################################
        # DISPATCH CLOUD TASKS SI BESOIN
        #######################################################################
        task_batch_limit = params.batch_size or 128

        if len(grouped_items) > task_batch_limit:
            logging.info(
                f"🎬 Dispatching vers Cloud Tasks : {len(grouped_items)} fichier(s) vidéo "
                f"({len(docs_ids_to_process)} documents) à traiter (> {task_batch_limit})"
            )

            #---- Découpage en lots de FICHIERS, pas de documents : tous les documents d'un même
            ## fichier doivent rester dans la même tâche, sinon la déduplication est perdue — deux
            ## tâches téléchargeraient et transcriberaient le même fichier. C'est la raison pour
            ## laquelle le seuil compare désormais des fichiers et non des documents.
            ## L'enveloppe de temps par tâche ne s'aggrave pas : avant, un lot de `batch_size`
            ## documents pouvait déjà être `batch_size` fichiers distincts (cas de vidéos toutes
            ## uniques). Ce pire cas devient simplement le cas garanti.
            group_batches = [
                grouped_items[i: i + task_batch_limit]
                for i in range(0, len(grouped_items), task_batch_limit)
            ]
            batches = [
                [doc_id for _blob, docs in group_batch for doc_id, _json in docs]
                for group_batch in group_batches
            ]
            #----
            task_ids_log = []

            base_payload = serialize_dates(params.model_dump(exclude_unset=True))
            base_payload.pop("importation_id", None)
            base_payload.pop("blob_urls", None)
            base_payload["parent_job_id"] = job_id

            for batch_ids in batches:
                task_payload = {
                    **base_payload,
                    "documents_ids": batch_ids,
                }

                t_id = make_task_id(task_payload)
                task_ids_log.append(t_id)

                task_name = f"j{job_id}-batch-t{t_id}"

                create_video_transcription_task(
                    cloud_task_client=cloud_task_client,
                    queue_name=params.queue_name,
                    payload=task_payload,
                    task_name=task_name,
                )

            timer.n_tasks_dispatched = len(task_ids_log)
            timer.emit({"model_was_cached": model_was_cached, "dispatch_type": "batch_split"})
            return {
                "message": f"Dispatch terminé. {len(task_ids_log)} tâches créées pour "
                f"{len(grouped_items)} fichier(s) vidéo ({len(docs_ids_to_process)} documents).",
                "job_id": job_id,
            }, 200

        #######################################################################
        # EXECUTION DIRECTE / WORKER
        #######################################################################
        logging.info(
            f"🎬 Exécution pour {len(grouped_items)} fichier(s) vidéo "
            f"({len(docs_data)} document(s) à mettre à jour)..."
        )

        ## Seul point de la fonction où le modèle est réellement nécessaire côté worker : après le
        ## dispatch, donc jamais chargé par une tâche qui ne fait que découper.
        whisperx_model, model_was_cached = _ensure_whisperx_model(
            params.whisperx_model, params.device, timer
        )

        ## Pré-chargement des documents scènes pour tous les documents à traiter
        with timer.sql_read():
            scenes_by_video_id = get_scene_documents_by_video_ids(db, docs_ids_to_process)

        errors = []
        processed_count = 0
        ## Nombre d'inférences WhisperX réellement lancées (= fichiers traités), distinct de
        ## processed_count qui compte les DOCUMENTS mis à jour. L'écart entre les deux est
        ## exactement le gain du regroupement par blob_url.
        transcriptions_run = 0
        transcribed_ids: list[int] = []
        docs_to_update: list[dict] = []

        #---- Boucle par FICHIER (blob_url), plus par document. Répartition du travail :
        ##   - niveau FICHIER, une seule fois : téléchargement GCS + WhisperX + whisper_segments.
        ##     C'est tout le coût (GPU + réseau) ;
        ##   - niveau DOCUMENT : les scènes viennent de video-scene-detection et sont propres à
        ##     CHAQUE document, donc `text` doit être recalculé par document — on ne peut pas le
        ##     recopier. Ce recalcul est du CPU pur (add_scenes_transcripts + un join de chaînes).
        ## Réutiliser `transcript_segments` pour tous les documents du groupe est sûr :
        ## `add_scenes_transcripts` ne fait que LIRE cette liste (elle construit ses propres
        ## `timed_units`) et ne mute que les dicts de `scenes`, reconstruits à chaque itération.
        for current_index, (blob_url, group_docs) in enumerate(grouped_items):
            local_path = None
            ## Calculé au plus une fois par fichier, et seulement si un document du groupe n'a pas
            ## de scènes pré-calculées (la durée est une propriété du fichier, pas du document).
            duration_ms = None
            ensure_memory_headroom_for_alignment_cache(
                priority_languages=params.priority_languages or [],
                device=params.device,
            )

            group_doc_ids = [doc_id for doc_id, _json in group_docs]
            logging.info(
                f"🎬 START blob_url={blob_url} | {len(group_docs)} document(s) : {group_doc_ids}"
            )

            try:
                local_path = download_gcs_url_to_tmp(
                    gs_url=blob_url,
                    storage_client=storage_client,
                )

                ## UNE inférence pour tous les documents qui pointent vers ce fichier
                with timer.inference():
                    transcript_segments, detected_language = transcribe_video(
                        video_path=local_path,
                        model=whisperx_model,
                        device=params.device,
                        language=params.transcription_language,
                    )
                transcriptions_run += 1
                if params.device == "cuda":
                    gpu_allocated_mb = torch.cuda.memory_allocated() // 1024**2
                    gpu_peak_mb = torch.cuda.max_memory_allocated() // 1024**2
                else:
                    gpu_allocated_mb = 0
                    gpu_peak_mb = 0
                logging.info(
                    f"🎬 Langue détectée: {detected_language} | "
                    f"Segments Whisper: {len(transcript_segments)} | "
                    f"blob_url={blob_url} | {len(group_docs)} document(s) à mettre à jour"
                )

                ## whisper_segments dépend du FICHIER : calculé une fois, écrit à l'identique sur
                ## chaque document du groupe (aucune mutation ensuite, le partage est sûr).
                whisper_segments = [
                    {"text": s.get("text", ""), "start": s["start"], "end": s["end"]}
                    for s in transcript_segments
                    if s.get("start") is not None and s.get("end") is not None
                ]

                for document_id, doc_json_data in group_docs:
                    #---- try/except par DOCUMENT. Indispensable depuis le regroupement : une erreur
                    ## à ce niveau (scènes malformées, durée illisible, découpage du transcript) ne
                    ## concerne QUE ce document. Sans ce niveau elle remonterait au `except` du
                    ## fichier, qui reporterait un échec sur TOUS les documents du groupe — y compris
                    ## ceux déjà ajoutés à docs_to_update, donc à la fois écrits en base ET comptés
                    ## en échec. On conserve ainsi exactement la granularité d'avant : un document
                    ## en erreur n'entraîne pas ses voisins.
                    try:
                        ## Lecture des scènes pré-calculées par la CF video-scene-detection
                        raw_scenes = scenes_by_video_id.get(document_id) or []
                        has_precomputed_scenes = bool(raw_scenes)

                        if has_precomputed_scenes:
                            scenes = [
                                Scene(
                                    index=s["scene_index"],
                                    start_frame=s["start_frame"],
                                    end_frame=s["end_frame"],
                                    start_ms=s["start_ms"],
                                    end_ms=s["end_ms"],
                                    transcript=s["transcript"],
                                )
                                for s in raw_scenes
                            ]
                        else:
                            if duration_ms is None:
                                duration_ms = get_media_duration_ms(local_path)
                            scenes = [
                                Scene(
                                    index=0,
                                    start_frame=0,
                                    end_frame=-1,
                                    start_ms=0,
                                    end_ms=duration_ms,
                                    transcript="",
                                )
                            ]
                            logging.info(
                                f"🎬 Pas de scènes pré-calculées → scène unique "
                                f"(durée: {duration_ms / 1000:.1f}s) | document_id={document_id}"
                            )

                        scenes = add_scenes_transcripts(scenes, transcript_segments)

                        video_transcription = " ".join(
                            scene["transcript"].strip()
                            for scene in scenes
                            if scene["transcript"] and scene["transcript"].strip()
                        )

                        ## Mise à jour du texte du document vidéo
                        doc_update = {
                            "id": document_id,
                            "text": video_transcription,
                        }

                        if detected_language:
                            doc_update["lang"] = detected_language
                            doc_update["text_fr"] = video_transcription if detected_language == "fr" else None
                            doc_update["text_en"] = video_transcription if detected_language == "en" else None
                            doc_update["text_ru"] = video_transcription if detected_language == "ru" else None

                        updated_json_data = dict(doc_json_data) if doc_json_data else {}
                        updated_json_data["whisper_segments"] = whisper_segments
                        doc_update["json_data"] = updated_json_data
                        docs_to_update.append(doc_update)

                        ## Mise à jour des documents scènes (text + lang)
                        if has_precomputed_scenes:
                            for scene, scene_db in zip(scenes, raw_scenes):
                                scene_text = scene["transcript"]
                                if scene_text:
                                    scene_update = {
                                        "id": scene_db["scene_doc_id"],
                                        "text": scene_text,
                                    }
                                    if detected_language:
                                        scene_update["lang"] = detected_language
                                        scene_update["text_fr"] = scene_text if detected_language == "fr" else None
                                        scene_update["text_en"] = scene_text if detected_language == "en" else None
                                        scene_update["text_ru"] = scene_text if detected_language == "ru" else None
                                    docs_to_update.append(scene_update)

                        processed_count += 1
                        transcribed_ids.append(document_id)

                    except Exception as e:
                        error_data = {
                            "document_id": document_id,
                            "blob_url": blob_url,
                            "error": f"🎬 {str(e)}",
                        }
                        logging.error(error_data)
                        errors.append(error_data)
                    #----

            except Exception as e:
                ## Échec au niveau du FICHIER — uniquement le téléchargement GCS ou l'inférence
                ## WhisperX, puisque le traitement par document a son propre try/except ci-dessus.
                ## Aucun document du groupe n'a pu être traité : on reporte l'échec sur chacun,
                ## comme le faisait la boucle par document avant le regroupement.
                for document_id in group_doc_ids:
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

                if is_near_cloud_task_timeout(worker_start_time_monotonic):
                    #---- La reprise repart du prochain FICHIER non traité. Comme tous les documents
                    ## d'un même fichier sont dans le même groupe, aucun ne peut être oublié ni
                    ## retraité par la tâche de reprise. `remaining_docs` reste une liste de
                    ## document_id : c'est ce qu'attendent le payload de reprise et le compteur
                    ## `remaining_count` de la réponse.
                    remaining_docs = [
                        doc_id
                        for _blob, docs in grouped_items[current_index + 1:]
                        for doc_id, _json in docs
                    ]
                    #----

                    with timer.sql_write(n_items=len(docs_to_update)):
                        bulk_update_documents(db=db, documents_to_update=docs_to_update)
                    docs_to_update = []

                    transcribed_ids = []

                    if remaining_docs:
                        remaining_document_ids = remaining_docs

                        next_task_payload = serialize_dates(params.model_dump(exclude_unset=True))
                        next_task_payload.pop("importation_id", None)
                        next_task_payload.pop("blob_urls", None)
                        next_task_payload["parent_job_id"] = job_id
                        next_task_payload["documents_ids"] = remaining_document_ids

                        next_task_id = make_task_id(next_task_payload)
                        next_task_name = f"j{job_id}-resume-{next_task_id}"

                        create_video_transcription_task(
                            cloud_task_client=cloud_task_client,
                            queue_name=params.queue_name,
                            payload=next_task_payload,
                            task_name=next_task_name,
                        )

                        logging.warning(
                            f"⏳ Timeout proche. Relance planifiée avec "
                            f"{len(remaining_document_ids)} documents restants | "
                            f"next_task_name={next_task_name}"
                        )

                    if not remaining_docs:
                        if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
                            _fire_pipeline_callback(params.call_back_pipeline)

                    timer.n_items_written = processed_count
                    timer.emit({"model_was_cached": model_was_cached, "gpu_memory_allocated_mb": gpu_allocated_mb, "gpu_memory_peak_mb": gpu_peak_mb, "scenario": "worker_timeout", "transcriptions_run": transcriptions_run})
                    return {
                        "message": "Succès partiel avant timeout. Reprise planifiée.",
                        "processed_count": processed_count,
                        ## Inférences WhisperX réellement lancées : < processed_count dès qu'un
                        ## fichier est partagé par plusieurs documents.
                        "transcriptions_run": transcriptions_run,
                        "failed_count": len(errors),
                        "remaining_count": len(remaining_docs),
                        "job_id": job_id,
                    }, 200

        with timer.sql_write(n_items=len(docs_to_update)):
            bulk_update_documents(db=db, documents_to_update=docs_to_update)

        if params.call_back_pipeline and _is_last_task_in_queue(params.queue_name, job_id):
            _fire_pipeline_callback(params.call_back_pipeline)

        timer.n_items_written = processed_count
        timer.emit({"model_was_cached": model_was_cached, "gpu_memory_allocated_mb": gpu_allocated_mb, "gpu_memory_peak_mb": gpu_peak_mb, "scenario": "worker_complete", "transcriptions_run": transcriptions_run})
        logging.info(
            f"🎬 Terminé : {processed_count} document(s) mis à jour pour "
            f"{transcriptions_run} inférence(s) WhisperX | {len(errors)} échec(s)"
        )
        return {
            "message": "Succès",
            "processed_count": processed_count,
            "transcriptions_run": transcriptions_run,
            "failed_count": len(errors),
            "job_id": job_id,
        }, 200

    except Exception as e:
        logging.error(f"🎬 Critical Error: {e}")
        logging.error(traceback.format_exc())
        if db:
            db.rollback()
        try:
            timer.emit({"error": str(e)})  # type: ignore[name-defined]
        except Exception:
            pass
        return f"Error: {e}", 500

    finally:
        if db:
            db.close()
        log_memory("end_of_task")
        

def _is_last_task_in_queue(queue_name: str, job_id: str) -> bool:
    """Renvoie True si cette tâche est la dernière active pour ce job dans la queue."""
    from google.cloud import tasks_v2
    project_id = os.environ.get("GCLOUD_PROJECT", "demo-project")
    try:
        client = tasks_v2.CloudTasksClient()
        queue_path = client.queue_path(project_id, "europe-west1", queue_name)
        job_marker = f"j{job_id}"
        active_count = sum(
            1 for task in client.list_tasks(
                request={"parent": queue_path, "response_view": tasks_v2.Task.View.BASIC}
            )
            if job_marker in task.name.split("/")[-1]
        )
        return active_count <= 1  # 1 = soi-même encore "running"
    except Exception as e:
        logging.warning(f"🎬 _is_last_task_in_queue error (conservative: no callback): {e}")
        return False


def _fire_pipeline_callback(call_back_pipeline: dict) -> None:
    """Crée la Cloud Task de callback vers pipeline-topic-modeling."""
    try:
        client = CloudTasks()
        task = CloudTask(
            queue_name=call_back_pipeline["queue_name"],
            name=call_back_pipeline["task_name"],
            http_method="POST",
            url=call_back_pipeline["url"],
            payload=call_back_pipeline["payload"],
            include_oidc_token=True,
            timeout=1800,
        )
        client.add_task(task)
        logging.info(f"🎬 Pipeline callback fired | task_name={call_back_pipeline['task_name']}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logging.info(f"🎬 Pipeline callback already fired (dedup OK) | task_name={call_back_pipeline['task_name']}")
        else:
            logging.warning(f"🎬 Pipeline callback error (non-fatal) | {e}")


def create_video_transcription_task(
    cloud_task_client: CloudTasks,
    queue_name: str,
    payload: dict,
    task_name: str,
):
    task = CloudTask(
        queue_name=queue_name,
        name=task_name,
        http_method="POST",
        url=CLOUD_TASK_HANDLER_URL,
        payload=payload,
        include_oidc_token=True,
        timeout=CLOUD_TASK_TIMEOUT_SECONDS,
    )
    cloud_task_client.add_task(task)

    logging.info(
        f"✅ Cloud Task créée | task_name={task_name} | queue_name={queue_name}"
    )


def is_near_cloud_task_timeout(start_time_monotonic: float) -> bool:
    elapsed_seconds = time.monotonic() - start_time_monotonic
    remaining_time_seconds = CLOUD_TASK_TIMEOUT_SECONDS - elapsed_seconds

    return remaining_time_seconds <= CLOUD_TASK_TIMEOUT_SAFETY_MARGIN_SECONDS
    
def log_memory(stage: str):
    try:
        process = psutil.Process(os.getpid())

        mem_info = process.memory_info()
        rss_mb = mem_info.rss / 1024 / 1024

        _, tmp_used, tmp_free = shutil.disk_usage("/tmp")
        tmp_used_mb = tmp_used / 1024 / 1024
        tmp_free_mb = tmp_free / 1024 / 1024

        logging.info(
            f"🧠 MEMORY | stage={stage} | "
            f"process_rss_mb={rss_mb:.2f} | "
            f"/tmp_used_mb={tmp_used_mb:.2f} | /tmp_free_mb={tmp_free_mb:.2f}"
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

