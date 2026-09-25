from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from gcpOpsci.models import Document


VIEW_POSTS_WITH_MEDIA = "public.v_posts_and_quotes_forwards_wt_medias"

# Formats vidéo supportés par OpenCV — synchronisé avec OPENCV_VIDEO_EXTENSIONS dans video-scene-detection/utils/crud_documents.py
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm")


def _sql_video_extension_filter(col: str) -> str:
    return "(" + " OR ".join(f"LOWER({col}) LIKE '%{ext}'" for ext in VIDEO_EXTENSIONS) + ")"


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
) -> list[tuple[int, str, dict | None]]:
    """
    Retourne : (id, blob_url, json_data)
    Utilisé dans le scénario worker quand on a déjà la liste des IDs.
    """
    if not document_ids:
        return []

    query = (
        select(Document.id, Document.blob_url, Document.json_data)
        .where(
            Document.id.in_(document_ids),
            Document.blob_url.is_not(None),
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
    include_transcribed: bool = False,
    video_only: bool = True,
) -> list[tuple[int, str, dict | None]]:
    """
    Retourne : (id, blob_url, json_data)

    Par défaut, exclut les documents déjà transcrits (text IS NOT NULL).
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
    ]

    if video_only:
        conditions.append(_sql_video_extension_filter("document_blob_url"))

    params = {
        "post_types": post_types,
    }

    if start_date is not None:
        conditions.append("source_date >= :start_date")
        params["start_date"] = start_date

    if end_date is not None:
        conditions.append("source_date <= :end_date")
        params["end_date"] = end_date

    if not include_transcribed:
        conditions.append(
            f"""
            document_id NOT IN (
                SELECT id
                FROM documents
                WHERE importation_id IN ({_imp_ids_sql})
                  AND text IS NOT NULL
            )
            """
        )

    query = text(f"""
        SELECT DISTINCT v.document_id AS id, v.document_blob_url AS blob_url, d.json_data
        FROM {VIEW_POSTS_WITH_MEDIA} v
        JOIN documents d ON d.id = v.document_id
        WHERE {" AND ".join(conditions)}
    """).bindparams(
        bindparam("post_types", expanding=True)
    )

    return db.execute(query, params).all()


def get_scene_documents_by_video_ids(
    db: Session,
    video_ids: list[int],
) -> dict[int, list[dict]]:
    """
    Retourne pour chaque video_id la liste de ses documents scènes ordonnés par scene_index.

    Chaque scène contient : scene_doc_id, scene_index, start_ms, end_ms, start_frame, end_frame, transcript.
    """
    if not video_ids:
        return {}

    query = text("""
        SELECT
            jdd.from_document_id                              AS video_id,
            d.id                                              AS scene_doc_id,
            COALESCE((d.json_data->>'scene_index')::int, 0)   AS scene_index,
            COALESCE((d.json_data->>'start_ms')::int, 0)      AS start_ms,
            COALESCE((d.json_data->>'end_ms')::int, 0)        AS end_ms,
            COALESCE((d.json_data->>'start_frame')::int, 0)   AS start_frame,
            COALESCE((d.json_data->>'end_frame')::int, 0)     AS end_frame,
            d.text                                            AS transcript
        FROM join_document_document jdd
        JOIN documents d ON d.id = jdd.to_document_id
        WHERE jdd.type = 'scene'
          AND jdd.from_document_id IN :video_ids
          AND d.type = 'scene'
    """).bindparams(bindparam("video_ids", expanding=True))

    rows = db.execute(query, {"video_ids": list(video_ids)}).fetchall()

    result: dict[int, list[dict]] = {}
    for row in rows:
        vid = row.video_id
        if vid not in result:
            result[vid] = []
        result[vid].append({
            "scene_doc_id": row.scene_doc_id,
            "scene_index": row.scene_index,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
            "start_frame": row.start_frame,
            "end_frame": row.end_frame,
            "transcript": row.transcript or "",
        })

    for scenes in result.values():
        scenes.sort(key=lambda s: s["scene_index"])

    return result


def bulk_update_documents(
    db: Session,
    documents_to_update: list[dict],
) -> None:
    if not documents_to_update:
        return

    db.bulk_update_mappings(Document, documents_to_update)
    db.commit()


