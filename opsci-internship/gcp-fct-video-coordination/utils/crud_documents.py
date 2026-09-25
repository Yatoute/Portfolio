from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from gcpOpsci.models import ChunkSelfAssociation

from utils.types import ChunkCoordinationRow, SceneEmbeddingRow


def get_scene_embeddings(
    db: Session,
    analyse_id: int,
) -> list[SceneEmbeddingRow]:
    """
    Charge les embeddings CLIP des scènes depuis analyses_chunks.

    Les scene chunks ont ac.document_id = video doc ID (type='video').
    video_id = ac.document_id directement — pas de résolution nécessaire.
    Les transcriptions sont récupérées séparément via get_scene_transcripts_by_chunk_ids.
    """
    query = text("""
        SELECT
            ac.id             AS chunk_id,
            ac.document_id    AS video_id,
            ac.start_position AS start_frame,
            ac.end_position   AS end_frame,
            ac.vector
        FROM analyses_chunks ac
        JOIN documents d_video
          ON d_video.id   = ac.document_id
         AND d_video.type = 'video'
        WHERE ac.analyse_id = :analyse_id
          AND ac.vector IS NOT NULL
        ORDER BY video_id, ac.start_position
    """).bindparams(bindparam("analyse_id"))

    rows = db.execute(query, {"analyse_id": analyse_id}).fetchall()
    return [row._asdict() for row in rows]


def get_scene_transcripts_by_chunk_ids(
    db: Session,
    chunk_ids: list[int],
) -> dict[int, str]:
    """Retourne {chunk_id: transcript} via le document scène associé au chunk."""
    if not chunk_ids:
        return {}

    rows = db.execute(text("""
        SELECT ac.id AS chunk_id, d.text AS transcript
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

    return {row.chunk_id: (row.transcript or "") for row in rows}


def persist_coordination_to_db(
    db: Session,
    rows: list[ChunkCoordinationRow],
) -> int:
    """Insère ou met à jour les paires de coordination dans join_chunk_chunk."""
    if not rows:
        return 0

    insert_stmt = pg_insert(ChunkSelfAssociation.__table__).values(rows)
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["from_chunk_id", "to_chunk_id", "type"],
        set_={
            "similarity":         insert_stmt.excluded.similarity,
            "coordination_score": insert_stmt.excluded.coordination_score,
            "json_data":          insert_stmt.excluded.json_data,
        },
    )
    db.execute(stmt)
    db.commit()

    return len(rows)
