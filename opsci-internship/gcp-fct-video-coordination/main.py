"""⚠️ DÉPRÉCIÉ — Cloud Function `video-coordination`.

La détection de reprises de scènes vidéo est désormais intégrée à la Cloud Function
`coordination`, accessible via `data_type='video'`. Cette version unifiée bénéficie en
plus des modes lot/incrémental (fenêtre temporelle), de l'agrégation document↔document,
du recalcul des `component_id` (composantes connexes) et du scoring de désinformation.

Cette fonction est conservée pour compatibilité mais ne devrait plus être appelée.
Préférer : POST coordination { "analyse_id": ..., "data_type": "video" }.
"""

import gc
import logging
from typing import Optional

from flask import Request
from pydantic import BaseModel, Field, ValidationError

from gcpOpsci.db import get_db
from gcpOpsci.cloud_tasks import CloudTask, CloudTasks
from gcpOpsci.tools import log_and_return_response, make_task_id, PerfTimer

from utils.coordination import build_clip_index, build_coordination_rows, find_sequence_reprises, tag_noise_groups
from utils.crud_documents import get_scene_embeddings, persist_coordination_to_db


SERVICE_URL = (
    "https://europe-west1-demo-project.cloudfunctions.net/video-coordination"
)


class RequestParams(BaseModel):
    """Schéma de validation des paramètres de la requête HTTP"""

    ## Analyse
    analyse_id: int = Field(..., description="Identifiant de l'analyse visuelle (scènes, keyframes CLIP)")

    ## Paramètres de l'algorithme de détection
    clip_threshold: float = Field(0.97, description="Seuil de similarité cosinus CLIP strict [0-1]")
    ext_threshold: Optional[float] = Field(
        0.90,
        description="Seuil de similarité faible pour le gap bridging. Si None : clip_threshold - 0.05",
    )
    batch_size: int = Field(512, description="Taille des batchs pour le calcul matriciel de similarité")
    uniform_norm_threshold: float = Field(
        0.97,
        description=(
            "Seuil de norme brute pour détecter les scènes visuellement uniformes (écran noir/blanc, image de fin). "
            "Appliqué uniquement aux séquences de longueur 1. "
            "Une norme proche de 1 signale des keyframes quasi-identiques au sein de la scène."
        ),
    )

    ## Cloud Tasks
    queue_name: Optional[str] = Field("video-coordination", description="Nom de la queue Cloud Tasks")
    parent_job_id: Optional[str] = Field(None, description="Identifiant du job parent pour le suivi")


@log_and_return_response
def run(request: Request):
    logging.warning(
        "⚠️ DÉPRÉCIÉ : video-coordination est remplacée par coordination?data_type=video "
        "(modes lot/incrémental, doc↔doc, component_id, scoring désinfo)."
    )
    try:
        params = RequestParams(**(request.json if request.method == "POST" else request.args))
        logging.info(f"🎞️ Nouvelle requête : {params}")
    except ValidationError as e:
        return f"Paramètres invalides : {e}", 400

    ## Génération du job_id
    if params.parent_job_id is None:
        job_id = make_task_id(params.model_dump(exclude_unset=True))
    else:
        job_id = params.parent_job_id
    logging.info(f"🎞️ Démarrage job_id={job_id} | analyse_id={params.analyse_id}")

    ## Fire-and-forget
    if params.parent_job_id is None:
        logging.info(f"🎞️ [FIRE-AND-FORGET] Dispatching job {job_id} vers Cloud Tasks...")
        cloud_task_client = CloudTasks()
        task_payload = params.model_dump(exclude_unset=True)
        task_payload["parent_job_id"] = job_id
        task_name = f"va{params.analyse_id}-j{job_id}-init"
        task = CloudTask(
            queue_name=params.queue_name,
            name=task_name,
            http_method="POST",
            url=SERVICE_URL,
            payload=task_payload,
            include_oidc_token=True,
            timeout=60 * 60 * 0.5,
        )
        cloud_task_client.add_task(task)
        logging.info(f"🎞️ Tâche Cloud Tasks créée : {task_name}")
        return {
            "status": "accepted",
            "job_id": job_id,
            "message": "Job dispatché vers Cloud Tasks.",
        }, 202

    ## PerfTimer
    retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", 0))
    timer = PerfTimer(function_name="video-coordination", parent_job_id=params.parent_job_id, retry_count=retry_count)

    ## Exécution principale
    db = None
    try:
        with timer.db_connect():
            db = next(get_db())

        ## Chargement des embeddings CLIP depuis la BDD
        logging.info(f"🎞️ Chargement des embeddings CLIP (analyse {params.analyse_id})...")
        with timer.sql_read():
            rows = get_scene_embeddings(db, params.analyse_id)
        timer.n_items_read = len(rows)
        logging.info(f"🎞️ {len(rows)} scènes chargées")

        clip_index = build_clip_index(rows)
        del rows
        gc.collect()
        logging.info(f"🎞️ clip_index construit : {len(clip_index)} vidéos")

        if not clip_index:
            timer.emit({"message": "no_embeddings"})
            return {
                "status":  "success",
                "job_id":  job_id,
                "message": "Aucun embedding disponible pour cette analyse.",
                "n_coordinations": 0,
            }, 200

        ## Détection des séquences reprises
        logging.info(f"🎞️ Détection des reprises (threshold={params.clip_threshold})...")
        with timer.inference():
            groups = find_sequence_reprises(
                clip_index=clip_index,
                threshold=params.clip_threshold,
                ext_threshold=params.ext_threshold,
                batch_size=params.batch_size,
            )

            ## Filtrage du bruit
            tagged_groups = tag_noise_groups(groups, db=db, uniform_norm_threshold=params.uniform_norm_threshold)

        ## Persistance des paires de coordination en base
        logging.info(f"🎞️ Persistance des paires de coordination...")
        coordination_rows = build_coordination_rows(tagged_groups, clip_index)
        with timer.sql_write(n_items=len(coordination_rows)):
            n_coordinations = persist_coordination_to_db(db, coordination_rows)
        timer.n_items_written = n_coordinations

        timer.emit()
        return {
            "status":          "success",
            "job_id":          job_id,
            "n_coordinations": n_coordinations,
        }, 200

    except Exception as e:
        if db is not None:
            db.rollback()
        logging.error(f"🎞️ Erreur : {e}")
        timer.emit({"error": str(e)})
        raise
    finally:
        if db is not None:
            db.close()
