import math
import numpy as np

from watchagent.perception.scene_features import (
    frame_change_score,
    to_lowres_grayscale,
)
from watchagent.settings import DEFAULT_FPS, SAMPLER_TARGET_FPS


def add_temporal_stability_scores(
    indexed_frames: list[tuple[int, np.ndarray]],
) -> list[tuple[int, np.ndarray, float]]:
    if not indexed_frames:
        return []

    if len(indexed_frames) == 1:
        abs_idx, frame = indexed_frames[0]
        return [(abs_idx, frame, 0.0)]

    lowres_frames = [
        (abs_idx, frame, to_lowres_grayscale(frame))
        for abs_idx, frame in indexed_frames
    ]

    scored_frames: list[tuple[int, np.ndarray, float]] = []

    for i, (abs_idx, frame, current_lowres) in enumerate(lowres_frames):
        if i == 0:
            stability_score = frame_change_score(
                current_lowres,
                lowres_frames[i + 1][2],
            )
        elif i == len(lowres_frames) - 1:
            stability_score = frame_change_score(
                current_lowres,
                lowres_frames[i - 1][2],
            )
        else:
            previous_change = frame_change_score(
                current_lowres,
                lowres_frames[i - 1][2],
            )
            next_change = frame_change_score(
                current_lowres,
                lowres_frames[i + 1][2],
            )
            stability_score = previous_change + next_change

        scored_frames.append((abs_idx, frame, stability_score))

    return scored_frames



def select_temporally_stable_frames(
    indexed_frames: list[tuple[int, np.ndarray]],
    fps: float = DEFAULT_FPS,
    target_fps: float = SAMPLER_TARGET_FPS,
    edge_margin_ratio: float = 0.0,
) -> list[tuple[int, np.ndarray]]:
    """
    Sélectionne des frames stables en respectant une densité temporelle locale.

    Principe :
    - découpe la scène en fenêtres d'environ 1 seconde
    - dans chaque fenêtre, sélectionne les frames les plus stables
    - garantit une couverture temporelle plus homogène qu'un tri global

    Retourne :
        [(abs_idx, frame), ...]
    """
    if not indexed_frames:
        return []

    n_frames = len(indexed_frames)
    if n_frames <= 2:
        return indexed_frames

    if fps <= 0:
        fps = DEFAULT_FPS

    # Marge optionnelle en début/fin
    edge_margin = int(round(n_frames * edge_margin_ratio))
    if 2 * edge_margin >= n_frames:
        edge_margin = 0

    candidate_frames = (
        indexed_frames[edge_margin:n_frames - edge_margin]
        if edge_margin > 0
        else indexed_frames
    )

    if len(candidate_frames) <= 2:
        candidate_frames = indexed_frames

    # Taille d'une fenêtre = ~1 seconde
    window_size = max(1, int(round(fps)))

    # Nombre de frames à garder par fenêtre d'1 seconde 
    keep_per_window = max(1, int(round(target_fps)))

    selected_frames: list[tuple[int, np.ndarray]] = []

    for start in range(0, len(candidate_frames), window_size):
        window = candidate_frames[start:start + window_size]

        if not window:
            continue

        if len(window) <= keep_per_window:
            selected_frames.extend(window)
            continue

        scored_window = add_temporal_stability_scores(window)
        scored_window.sort(key=lambda x: x[2])  # score faible = plus stable

        best = scored_window[:keep_per_window]
        best.sort(key=lambda x: x[0])  # remettre dans l'ordre temporel

        selected_frames.extend((abs_idx, frame) for abs_idx, frame, _ in best)

    return selected_frames