"""
Orchestrates the three pipeline stages against a single locally-stored
video file, in-process — no GCS, no Postgres, no Cloud Tasks. This is the
one piece that has no equivalent in the production Cloud Functions: there,
each stage is a separate deployable unit reading/writing shared state in a
database. Here, one process runs all three stages sequentially against an
uploaded file and returns a single JSON-shaped result.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import torch

from pipeline.scenes import detect_scenes
from pipeline.keyframes import CLIPTextFrameSelector, compute_scene_data, select_video_keyframes
from pipeline.transcription import transcribe_video, add_scenes_transcripts

logger = logging.getLogger(__name__)

WHISPER_MODEL_NAME = os.environ.get("VIDEOMINING_WHISPER_MODEL", "small")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Loaded once per worker process, kept warm across requests.
_clip_selector: Optional[CLIPTextFrameSelector] = None
_whisper_model = None


def get_clip_selector() -> CLIPTextFrameSelector:
    global _clip_selector
    if _clip_selector is None:
        logger.info("Loading OpenCLIP ViT-B-32...")
        _clip_selector = CLIPTextFrameSelector(device=DEVICE)
    return _clip_selector


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisperx

        logger.info("Loading WhisperX model=%s device=%s...", WHISPER_MODEL_NAME, DEVICE)
        _whisper_model = whisperx.load_model(
            WHISPER_MODEL_NAME,
            DEVICE,
            compute_type="float16" if DEVICE == "cuda" else "int8",
        )
    return _whisper_model


@dataclass
class PipelineResult:
    metadata: dict
    scenes: list = field(default_factory=list)
    language: Optional[str] = None
    keyframes_dir: Optional[str] = None


ProgressFn = Callable[[str, float], None]


def _noop_progress(stage: str, fraction: float) -> None:
    return None


def process_video(
    video_path: str,
    keyframes_out_dir: str,
    *,
    max_keyframes_per_video: int = 24,
    on_progress: ProgressFn = _noop_progress,
) -> PipelineResult:
    os.makedirs(keyframes_out_dir, exist_ok=True)

    on_progress("scene_detection", 0.0)
    scenes, metadata = detect_scenes(video_path)
    on_progress("scene_detection", 1.0)

    clip_selector = get_clip_selector()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {video_path}")

    video_candidates = []
    try:
        for i, scene in enumerate(scenes):
            candidates, _scene_embedding = compute_scene_data(
                cap=cap,
                scene=scene,
                clip_selector=clip_selector,
                target_fps=3.0,
                max_candidates=8,
                max_frames_to_decode=400,
            )
            video_candidates.extend(candidates)
            on_progress("keyframes", (i + 1) / max(1, len(scenes)))
    finally:
        cap.release()

    selected = select_video_keyframes(
        video_candidates,
        max_keyframes=max_keyframes_per_video,
    )
    del video_candidates

    keyframes_by_scene: dict[int, list[dict]] = {s["index"]: [] for s in scenes}
    for rank, (frame_idx, frame, _clip_vec) in enumerate(selected):
        scene = _scene_for_frame(scenes, frame_idx)
        filename = f"kf_{rank:03d}_scene{scene['index']}.jpg"
        cv2.imwrite(os.path.join(keyframes_out_dir, filename), frame)
        keyframes_by_scene[scene["index"]].append({"rank": rank, "frame_index": int(frame_idx), "filename": filename})

    on_progress("transcription", 0.0)
    whisper_model = get_whisper_model()
    segments, language = transcribe_video(video_path, whisper_model, DEVICE)
    scenes = add_scenes_transcripts(scenes, segments)
    on_progress("transcription", 1.0)

    for scene in scenes:
        scene["keyframes"] = keyframes_by_scene.get(scene["index"], [])

    return PipelineResult(metadata=metadata, scenes=scenes, language=language, keyframes_dir=keyframes_out_dir)


def _scene_for_frame(scenes: list, frame_idx: int) -> dict:
    for scene in scenes:
        if scene["start_frame"] <= frame_idx < scene["end_frame"]:
            return scene
    return scenes[-1]
