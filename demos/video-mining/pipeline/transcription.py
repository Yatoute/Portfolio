"""
Transcription — vendored from the production `gcp-fct-video-transcription`
Cloud Function (WhisperX transcribe + align, then scene-aware splitting).

The production version wraps this in an LRU/memory-pressure eviction cache
for alignment models (many languages served concurrently on a long-lived
instance). Irrelevant here: this demo processes one video at a time and the
process is short-lived, so alignment models are just cached in a plain dict
for the lifetime of the worker.
"""

from __future__ import annotations

import gc
import re
from typing import List, Optional, Tuple, TypedDict

import torch
import whisperx
from whisperx.asr import FasterWhisperPipeline

_ALIGN_MODEL_CACHE: dict[str, tuple] = {}


class Scene(TypedDict):
    index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    transcript: str


def get_or_load_align_model(language_code: str, device: str):
    language_code = language_code.strip().lower()
    if language_code not in _ALIGN_MODEL_CACHE:
        _ALIGN_MODEL_CACHE[language_code] = whisperx.load_align_model(
            language_code=language_code, device=device
        )
    return _ALIGN_MODEL_CACHE[language_code]


def transcribe_video(
    video_path: str,
    model: FasterWhisperPipeline,
    device: str,
    batch_size: int = 8,
    language: Optional[str] = None,
) -> Tuple[List[dict], str]:
    """Transcrit une vidéo avec WhisperX puis aligne les segments mot-à-mot."""
    audio = None
    transcription_result = None
    alignment_result = None

    try:
        audio = whisperx.load_audio(video_path)

        transcription_result = model.transcribe(audio, batch_size=batch_size, language=language)
        detected_language = transcription_result["language"]

        align_model, align_metadata = get_or_load_align_model(detected_language, device=device)

        alignment_result = whisperx.align(
            transcription_result["segments"],
            align_model,
            align_metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        return alignment_result["segments"], detected_language

    finally:
        del alignment_result
        del transcription_result
        del audio
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def add_scenes_transcripts(
    scenes: List[Scene],
    whisper_segments: List[dict],
    use_words: bool = True,
    boundary_window: int = 15,
) -> List[Scene]:
    """
    Répartit les mots transcrits entre scènes, en ajustant la frontière au
    plus proche silence/ponctuation plutôt qu'une coupure mécanique au
    timestamp de scène — évite de couper un mot ou une phrase en deux.
    """
    scenes = sorted(scenes, key=lambda s: s["start_ms"])
    if not scenes:
        return scenes

    timed_units = []
    if use_words:
        for seg in whisper_segments:
            for w in seg.get("words", []) or []:
                text = (w.get("word") or "").strip()
                start, end = w.get("start"), w.get("end")
                if not text or start is None or end is None:
                    continue
                timed_units.append({"start_ms": int(float(start) * 1000), "end_ms": int(float(end) * 1000), "text": text})

    if not timed_units:
        for seg in whisper_segments:
            text = (seg.get("text") or "").strip()
            start, end = seg.get("start"), seg.get("end")
            if not text or start is None or end is None:
                continue
            timed_units.append({"start_ms": int(float(start) * 1000), "end_ms": int(float(end) * 1000), "text": text})

    if not timed_units:
        for scene in scenes:
            scene["transcript"] = ""
        return scenes

    timed_units.sort(key=lambda x: (x["start_ms"], x["end_ms"]))
    n_scenes = len(scenes)

    pause_pattern = re.compile(r"[.!?;]+[\"')\]]*$")
    strong_pause_pattern = re.compile(r"[.!?]+[\"')\]]*$")

    scene_token_indices = {scene["index"]: [] for scene in scenes}

    scene_ptr = 0
    for unit_idx, unit in enumerate(timed_units):
        start_ms = unit["start_ms"]
        while scene_ptr < n_scenes and scenes[scene_ptr]["end_ms"] <= start_ms:
            scene_ptr += 1
        if scene_ptr >= n_scenes:
            break
        scene_token_indices[scenes[scene_ptr]["index"]].append(unit_idx)

    for i in range(n_scenes - 1):
        current_scene, next_scene = scenes[i], scenes[i + 1]
        current_indices = sorted(scene_token_indices[current_scene["index"]])
        next_indices = sorted(scene_token_indices[next_scene["index"]])
        if not current_indices or not next_indices:
            continue

        left_indices = current_indices[-boundary_window:]
        right_indices = next_indices[:boundary_window]
        pivot_idx = right_indices[0]

        left_pause_idx = next(
            (idx for idx in reversed(left_indices) if pause_pattern.search(timed_units[idx]["text"].strip())), None
        )
        right_pause_idx = next(
            (idx for idx in right_indices if pause_pattern.search(timed_units[idx]["text"].strip())), None
        )

        cut_idx = None
        if left_pause_idx is not None and right_pause_idx is not None:
            left_distance = abs(left_pause_idx - pivot_idx)
            right_distance = abs(right_pause_idx - pivot_idx)
            if left_distance < right_distance:
                cut_idx = left_pause_idx
            elif right_distance < left_distance:
                cut_idx = right_pause_idx
            else:
                left_is_strong = bool(strong_pause_pattern.search(timed_units[left_pause_idx]["text"].strip()))
                right_is_strong = bool(strong_pause_pattern.search(timed_units[right_pause_idx]["text"].strip()))
                cut_idx = right_pause_idx if (right_is_strong and not left_is_strong) else left_pause_idx
        elif left_pause_idx is not None:
            cut_idx = left_pause_idx
        elif right_pause_idx is not None:
            cut_idx = right_pause_idx

        if cut_idx is None:
            continue

        affected_indices = left_indices + [idx for idx in right_indices if idx not in left_indices]
        for idx in affected_indices:
            if idx <= cut_idx:
                if idx in scene_token_indices[next_scene["index"]]:
                    scene_token_indices[next_scene["index"]].remove(idx)
                if idx not in scene_token_indices[current_scene["index"]]:
                    scene_token_indices[current_scene["index"]].append(idx)
            else:
                if idx in scene_token_indices[current_scene["index"]]:
                    scene_token_indices[current_scene["index"]].remove(idx)
                if idx not in scene_token_indices[next_scene["index"]]:
                    scene_token_indices[next_scene["index"]].append(idx)

    for scene in scenes:
        indices = sorted(scene_token_indices[scene["index"]], key=lambda idx: (timed_units[idx]["start_ms"], timed_units[idx]["end_ms"]))
        scene["transcript"] = " ".join(timed_units[idx]["text"] for idx in indices).strip()

    return scenes
