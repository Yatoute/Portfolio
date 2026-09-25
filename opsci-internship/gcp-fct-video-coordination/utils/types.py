from typing import Optional
from typing_extensions import TypedDict

import numpy as np


class SceneEmbeddingRow(TypedDict):
    """Ligne retournée par get_scene_embeddings() — une scène avec son embedding CLIP."""
    chunk_id:    int
    video_id:    int
    start_frame: int
    end_frame:   int
    vector:      list[float]


class SceneEntry(TypedDict):
    """Scène dans le clip_index — une entrée par (video_id, start_frame)."""
    chunk_id:        int
    scene_index:     int
    scene_embedding: np.ndarray


class VideoEntry(TypedDict):
    """Entrée du clip_index pour un document vidéo."""
    scenes: dict[int, SceneEntry]


ClipIndex = dict[int, VideoEntry]


class ChunkCoordinationRow(TypedDict):
    """Ligne prête à être insérée dans join_chunk_chunk."""
    from_chunk_id:      int
    to_chunk_id:        int
    type:               str
    similarity:         float
    coordination_score: float
    component_id:       str
    json_data:          dict
