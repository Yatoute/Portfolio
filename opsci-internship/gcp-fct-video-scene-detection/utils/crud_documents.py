from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from gcpOpsci.models import Document, Chunk
from gcpOpsci.models.document import DocumentSelfAssociation


VIEW_POSTS_WITH_MEDIA = "public.v_posts_and_quotes_forwards_wt_medias"

# Formats vidéo supportés par OpenCV — doit rester synchronisé avec VIDEO_EXTENSIONS dans video-transcription/main.py
OPENCV_VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm")


def _get_post_types(
    include_original: bool = True,
    include_quotes: bool = True,
    include_shares: bool = True,
    include_comments: bool = True,
) -> list[str]:
    post_types: list[str] = []

    if include_original:
        post_types.append("original")
    if include_quotes:
        post_types.append("quote")
    if include_shares:
        post_types.append("share")
    if include_comments:
        post_types.append("comment")

    return post_types


def get_documents_by_ids(
    db: Session,
    document_ids: Sequence[int],
) -> list[tuple[int, int, str]]:
    """
    Retourne : (importation_id, id, blob_url)
    Utilisé dans le scénario worker quand on a déjà la liste des IDs.
    """
    if not document_ids:
        return []

    query = (
        select(Document.importation_id, Document.id, Document.blob_url)
        .where(
            Document.id.in_(document_ids),
            Document.blob_url.is_not(None),
            or_(*[func.lower(Document.blob_url).like(f"%{ext}") for ext in OPENCV_VIDEO_EXTENSIONS]),
        )
    )
    return db.execute(query).all()


def get_video_documents(
    db: Session,
    *,
    importation_ids: list[int],
    start_date: datetime | str | None = None,
    end_date: datetime | str | None = None,
    include_original: bool = True,
    include_quotes: bool = True,
    include_shares: bool = True,
    include_comments: bool = True,
    include_scene_detected: bool = False,
) -> list[tuple[int, int, str]]:
    """
    Retourne : (importation_id, id, blob_url)

    Par défaut (include_scene_detected=False), exclut les documents dont la
    détection de scènes a déjà été effectuée (entrée dans join_document_document de type 'scene').
    """
    post_types = _get_post_types(
        include_original=include_original,
        include_quotes=include_quotes,
        include_shares=include_shares,
        include_comments=include_comments,
    )

    if not post_types:
        return []

    _imp_ids_sql = ",".join(str(int(i)) for i in importation_ids)
    conditions = [
        f"post_importation_id IN ({_imp_ids_sql})",
        "document_id IS NOT NULL",
        "document_blob_url IS NOT NULL",
        "document_type = 'video'",
        "join_post_post_type IN :post_types",
        "(" + " OR ".join(f"LOWER(document_blob_url) LIKE '%{ext}'" for ext in OPENCV_VIDEO_EXTENSIONS) + ")",
    ]

    params = {
        "post_types": post_types,
    }

    if start_date is not None:
        conditions.append("source_date >= :start_date")
        params["start_date"] = start_date

    if end_date is not None:
        conditions.append("source_date <= :end_date")
        params["end_date"] = end_date

    if not include_scene_detected:
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM join_document_document jdd_check
                WHERE jdd_check.from_document_id = document_id
                  AND jdd_check.type = 'scene'
            )
            """
        )

    query = text(f"""
        SELECT DISTINCT
            post_importation_id AS importation_id,
            document_id AS id,
            document_blob_url AS blob_url
        FROM {VIEW_POSTS_WITH_MEDIA}
        WHERE {" AND ".join(conditions)}
    """).bindparams(
        bindparam("post_types", expanding=True)
    )

    return db.execute(query, params).all()


def persist_scene_detection_batch(
    db: Session,
    video_scenes_batch: list[tuple[int, int, list]],
    analyse_id: int,
) -> None:
    """
    Crée en batch les documents scènes, les jointures video→scene et les chunks de scènes.

    video_scenes_batch : liste de (video_id, importation_id, scenes)
        scenes : liste de dicts avec index, start_ms, end_ms, start_frame, end_frame
    """
    if not video_scenes_batch:
        return

    ## Aplatir toutes les scènes pour l'insertion en batch
    flat_doc_rows: list[dict] = []
    flat_chunk_rows: list[dict] = []
    video_scene_ranges: list[tuple[int, int, int]] = []  # (video_id, start_idx, end_idx)

    for video_id, importation_id, scenes in video_scenes_batch:
        start_idx = len(flat_doc_rows)
        for scene in scenes:
            flat_doc_rows.append({
                "type": "scene",
                "importation_id": importation_id,
                "json_data": {
                    "scene_index": scene["index"],
                    "start_ms": scene["start_ms"],
                    "end_ms": scene["end_ms"],
                    "start_frame": scene["start_frame"],
                    "end_frame": scene["end_frame"],
                },
            })
            flat_chunk_rows.append({
                "document_id": video_id,
                "analyse_id": analyse_id,
                "start_position": scene["start_frame"],
                "end_position": scene["end_frame"],
            })
        end_idx = len(flat_doc_rows)
        video_scene_ranges.append((video_id, start_idx, end_idx))

    if not flat_doc_rows:
        return

    ## Insertion en batch des documents scènes — l'ordre des IDs retournés correspond à l'ordre d'insertion
    result = db.execute(insert(Document).returning(Document.id), flat_doc_rows)
    scene_ids = [row[0] for row in result]

    ## Construction des lignes de jointure video→scene
    join_rows: list[dict] = []
    for video_id, start_idx, end_idx in video_scene_ranges:
        for scene_id in scene_ids[start_idx:end_idx]:
            join_rows.append({
                "from_document_id": video_id,
                "to_document_id": scene_id,
                "type": "scene",
                "component_id": "video",
            })

    db.execute(
        pg_insert(DocumentSelfAssociation)
        .values(join_rows)
        .on_conflict_do_nothing(constraint="uq_doc_doc_type_component")
    )

    db.execute(
        pg_insert(Chunk)
        .values(flat_chunk_rows)
        .on_conflict_do_nothing()
    )

    db.commit()


def repopulate_scene_chunks_from_existing(
    db: Session,
    importation_ids: list[int],
    scenes_analyse_id: int,
) -> int:
    """
    Pour les vidéos des importations dont les scènes sont déjà détectées
    (entrées dans join_document_document.type='scene') mais absentes de
    analyses_chunks pour ce scenes_analyse_id, insère les chunks directement
    depuis les documents scène existants — sans re-télécharger aucune vidéo.

    Idempotent (ON CONFLICT DO NOTHING). Retourne le nombre de chunks insérés.
    """
    _imp_ids_sql = ",".join(str(int(i)) for i in importation_ids)
    result = db.execute(text(f"""
        INSERT INTO analyses_chunks (document_id, analyse_id, start_position, end_position)
        SELECT
            jdd.from_document_id,
            :scenes_analyse_id,
            (d.json_data->>'start_frame')::int,
            (d.json_data->>'end_frame')::int
        FROM join_document_document jdd
        JOIN documents d ON d.id = jdd.to_document_id AND d.type = 'scene'
        WHERE jdd.type = 'scene'
          AND jdd.from_document_id IN (
              SELECT DISTINCT document_id
              FROM public.v_posts_and_quotes_forwards_wt_medias
              WHERE post_importation_id IN ({_imp_ids_sql})
                AND document_id IS NOT NULL
                AND document_type = 'video'
          )
        ON CONFLICT DO NOTHING
    """), {"scenes_analyse_id": scenes_analyse_id})
    db.commit()
    return result.rowcount


def bulk_update_documents(
    db: Session,
    documents_to_update: list[dict],
) -> None:
    if not documents_to_update:
        return

    db.bulk_update_mappings(Document, documents_to_update)
    db.commit()
