from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from gcpOpsci.models import Document, Chunk
from gcpOpsci.models.document import DocumentSelfAssociation


VIEW_POSTS_WITH_MEDIA = "public.v_posts_and_quotes_forwards_wt_medias"


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
    """
    if not document_ids:
        return []

    query = (
        select(Document.importation_id, Document.id, Document.blob_url)
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
    scenes_analyse_id: int,
    keyframes_analyse_id: int | None = None,
    start_date: datetime | str | None = None,
    end_date: datetime | str | None = None,
    include_original: bool = True,
    include_quotes: bool = True,
    include_shares: bool = True,
    include_comments: bool = True,
    include_keyframed: bool = False,
) -> list[tuple[int, int, str]]:
    """
    Retourne : (importation_id, id, blob_url)

    Idempotence (force_all=False, include_keyframed=False) — une vidéo est incluse si :
    - elle n'a pas encore de chunks dans analyses_chunks(scenes_analyse_id),
      OU au moins un chunk sans vecteur (embedding de scène manquant)
    - OU (si keyframes_analyse_id fourni) elle n'a pas encore de keyframes sélectionnées
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
        f"vp.post_importation_id IN ({_imp_ids_sql})",
        "vp.document_id IS NOT NULL",
        "vp.document_blob_url IS NOT NULL",
        "vp.document_type = 'video'",
        "vp.join_post_post_type IN :post_types",
        ## Seuls les documents ayant des scènes détectées sont traités
        """
        EXISTS (
            SELECT 1
            FROM join_document_document jdd_scene
            WHERE jdd_scene.from_document_id = d.id
              AND jdd_scene.type = 'scene'
        )
        """,
    ]

    params = {
        "post_types": post_types,
    }

    if start_date is not None:
        conditions.append("vp.source_date >= :start_date")
        params["start_date"] = start_date

    if end_date is not None:
        conditions.append("vp.source_date <= :end_date")
        params["end_date"] = end_date

    if not include_keyframed:
        params["scenes_analyse_id"] = scenes_analyse_id
        or_parts = [
            ## Scène : pas de chunks OU au moins un chunk sans vecteur
            """EXISTS (
                    SELECT 1 FROM analyses_chunks ac
                    WHERE ac.document_id = d.id AND ac.analyse_id = :scenes_analyse_id AND ac.vector IS NULL
                )
            """,
        ]
        if keyframes_analyse_id is not None:
            ## Keyframes : pas de chunks dans analyses_chunks(keyframes_analyse_id)
            params["keyframes_analyse_id"] = keyframes_analyse_id
            or_parts.append(
                """NOT EXISTS (
                    SELECT 1 FROM analyses_chunks ac_kf
                    WHERE ac_kf.document_id = d.id AND ac_kf.analyse_id = :keyframes_analyse_id
                )"""
            )
        conditions.append(f"({' OR '.join(or_parts)})")

    query = text(f"""
        SELECT DISTINCT d.importation_id AS importation_id, d.id AS id, d.blob_url AS blob_url
        FROM {VIEW_POSTS_WITH_MEDIA} vp
        JOIN documents d ON d.id = vp.document_id
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


def persist_keyframes_batch(
    db: Session,
    video_keyframes_batch: list[dict],
    analyse_id: int,
) -> None:
    """
    Crée en batch les documents keyframes, les jointures scene→keyframe et les chunks de keyframes.

    video_keyframes_batch : liste de dicts avec scene_doc_id, importation_id, keyframes
        keyframes : liste de {rank, frame_index, blob_url}
    """
    if not video_keyframes_batch:
        return

    flat_doc_rows: list[dict] = []
    flat_chunk_rows: list[dict] = []
    scene_kf_ranges: list[tuple[int, int, int, list, int, int]] = []  # (scene_doc_id, start_idx, end_idx, keyframes_meta, scene_start_frame, scene_end_frame)

    for entry in video_keyframes_batch:
        scene_doc_id = entry["scene_doc_id"]
        importation_id = entry["importation_id"]
        keyframes = entry["keyframes"]

        start_idx = len(flat_doc_rows)
        for kf in keyframes:
            flat_doc_rows.append({
                "type": "keyframe",
                "importation_id": importation_id,
                "blob_url": kf["blob_url"],
                "json_data": {
                    "rank": kf["rank"],
                    "frame_index": kf["frame_index"],
                },
            })
        end_idx = len(flat_doc_rows)

        sorted_entry_kfs = sorted(keyframes, key=lambda k: k["frame_index"])
        scene_start_frame = entry.get("scene_start_frame", sorted_entry_kfs[0]["frame_index"] if sorted_entry_kfs else 0)
        scene_end_frame = entry.get("scene_end_frame", sorted_entry_kfs[-1]["frame_index"] if sorted_entry_kfs else 0)
        scene_kf_ranges.append((scene_doc_id, start_idx, end_idx, keyframes, scene_start_frame, scene_end_frame))

    if not flat_doc_rows:
        return

    ## Insertion en batch des documents keyframes
    result = db.execute(insert(Document).returning(Document.id), flat_doc_rows)
    kf_doc_ids = [row[0] for row in result]

    ## Construction des jointures scene→keyframe et des chunks keyframe
    join_rows: list[dict] = []
    for scene_doc_id, start_idx, end_idx, keyframes, scene_start_frame, scene_end_frame in scene_kf_ranges:
        scene_kf_ids = kf_doc_ids[start_idx:end_idx]
        for kf_doc_id, kf in zip(scene_kf_ids, keyframes):
            join_rows.append({
                "from_document_id": scene_doc_id,
                "to_document_id": kf_doc_id,
                "type": "keyframe",
                "component_id": "video",
            })

        ## Chunks de keyframe : la première démarre au start_frame de la scène, la dernière finit au end_frame
        sorted_kfs = sorted(keyframes, key=lambda k: k["frame_index"])
        for i, kf in enumerate(sorted_kfs):
            start_pos = scene_start_frame if i == 0 else kf["frame_index"]
            end_pos = sorted_kfs[i + 1]["frame_index"] - 1 if i + 1 < len(sorted_kfs) else scene_end_frame
            flat_chunk_rows.append({
                "document_id":    scene_doc_id,
                "analyse_id":     analyse_id,
                "start_position": start_pos,
                "end_position":   end_pos,
                "vector":         kf.get("keyframe_embedding"),
            })

    db.execute(
        pg_insert(DocumentSelfAssociation)
        .values(join_rows)
        .on_conflict_do_nothing(constraint="uq_doc_doc_type_component")
    )

    if flat_chunk_rows:
        db.execute(
            pg_insert(Chunk)
            .values(flat_chunk_rows)
            .on_conflict_do_nothing()
        )

    db.commit()


def persist_video_keyframes_batch(
    db: Session,
    video_keyframes_batch: list[dict],
    analyse_id: int,
) -> None:
    """
    Crée en batch les documents keyframes, les jointures video→keyframe et les chunks vidéo.

    video_keyframes_batch : liste de dicts avec document_id, importation_id, total_frames, keyframes
        keyframes : liste de {rank, frame_index, blob_url, keyframe_embedding}
    Les chunks couvrent la durée vidéo entière sans chevauchement :
        chunk[0] : [0, kf[1].frame_index - 1]
        chunk[i] : [kf[i].frame_index, kf[i+1].frame_index - 1]
        chunk[-1]: [kf[-1].frame_index, total_frames - 1]
    """
    if not video_keyframes_batch:
        return

    flat_doc_rows: list[dict] = []
    video_kf_ranges: list[tuple[int, int, int, list[dict], int]] = []

    for entry in video_keyframes_batch:
        video_doc_id = entry["document_id"]
        importation_id = entry["importation_id"]
        total_frames = entry.get("total_frames", 0)
        keyframes = sorted(entry.get("keyframes", []), key=lambda k: k["rank"])

        if not keyframes:
            continue

        start_idx = len(flat_doc_rows)
        for kf in keyframes:
            flat_doc_rows.append({
                "type": "keyframe",
                "importation_id": importation_id,
                "blob_url": kf["blob_url"],
                "json_data": {
                    "rank": kf["rank"],
                    "frame_index": kf["frame_index"],
                },
            })
        end_idx = len(flat_doc_rows)
        video_kf_ranges.append((video_doc_id, start_idx, end_idx, keyframes, total_frames))

    if not flat_doc_rows:
        return

    result = db.execute(insert(Document).returning(Document.id), flat_doc_rows)
    kf_doc_ids = [row[0] for row in result]

    join_rows: list[dict] = []
    flat_chunk_rows: list[dict] = []

    for video_doc_id, start_idx, end_idx, sorted_kfs, total_frames in video_kf_ranges:
        kf_ids = kf_doc_ids[start_idx:end_idx]

        for kf_doc_id, kf in zip(kf_ids, sorted_kfs):
            join_rows.append({
                "from_document_id": video_doc_id,
                "to_document_id": kf_doc_id,
                "type": "keyframe",
                "component_id": "video",
            })

        for i, kf in enumerate(sorted_kfs):
            start_pos = 0 if i == 0 else kf["frame_index"]
            end_pos = (
                sorted_kfs[i + 1]["frame_index"] - 1
                if i + 1 < len(sorted_kfs)
                else max(total_frames - 1, kf["frame_index"])
            )
            flat_chunk_rows.append({
                "document_id":    video_doc_id,
                "analyse_id":     analyse_id,
                "start_position": start_pos,
                "end_position":   end_pos,
                "vector":         kf.get("keyframe_embedding"),
            })

    db.execute(
        pg_insert(DocumentSelfAssociation)
        .values(join_rows)
        .on_conflict_do_nothing(constraint="uq_doc_doc_type_component")
    )

    if flat_chunk_rows:
        db.execute(
            pg_insert(Chunk)
            .values(flat_chunk_rows)
            .on_conflict_do_nothing()
        )

    db.commit()


def bulk_update_scene_chunk_vectors(
    db: Session,
    vector_updates: list[dict],
    analyse_id: int,
) -> None:
    """
    Met à jour le vecteur des chunks de scènes dans analyses_chunks.
    vector_updates : liste de {video_id, start_frame, end_frame, vector}
    """
    if not vector_updates:
        return

    rows = [
        {
            "p_analyse_id": analyse_id,
            "p_video_id":   entry["video_id"],
            "p_start":      entry["start_frame"],
            "p_end":        entry["end_frame"],
            "p_vector":     entry["vector"],
        }
        for entry in vector_updates
    ]

    db.execute(
        text("""
            UPDATE analyses_chunks
               SET vector = :p_vector
             WHERE analyse_id    = :p_analyse_id
               AND document_id   = :p_video_id
               AND start_position = :p_start
               AND end_position   = :p_end
        """),
        rows,
    )

    db.commit()


def bulk_update_documents(
    db: Session,
    documents_to_update: list[dict],
) -> None:
    if not documents_to_update:
        return

    db.bulk_update_mappings(Document, documents_to_update)
    db.commit()
