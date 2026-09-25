import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")

import gc
import json
import re
import subprocess
from typing import List, Optional, Tuple, TypedDict

import torch
import whisperx
from whisperx.asr import FasterWhisperPipeline
from whisperx.schema import SingleAlignedSegment

from .cache import get_or_load_align_model


# =============================================================================
# TYPES
# =============================================================================

class Scene(TypedDict):
    index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    transcript: str


# =============================================================================
# DURÉE MÉDIA
# =============================================================================

def get_media_duration_ms(file_path: str) -> int:
    """Retourne la durée du fichier audio ou vidéo en millisecondes via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    return int(float(data["format"]["duration"]) * 1000)


# =============================================================================
# TRANSCRIPTION + ALIGNEMENT
# =============================================================================

def transcribe_video(
    video_path: str,
    model: FasterWhisperPipeline,
    device: str,
    batch_size: int = 8,
    language: Optional[str] = None,
) -> Tuple[List[dict], str]:
    """
    Transcrit une vidéo avec WhisperX puis aligne les segments.
    Le modèle d'alignement est récupéré depuis un cache global par langue.

    Retourne :
    - les segments alignés
    - la langue détectée
    """
    audio = None
    transcription_result = None
    alignment_result = None

    try:
        audio = whisperx.load_audio(video_path)

        transcription_result = model.transcribe(
            audio,
            batch_size=batch_size,
            language=language,
        )
        detected_language = transcription_result["language"]

        align_model, align_metadata = get_or_load_align_model(
            language_code=detected_language,
            device=device,
        )

        alignment_result = whisperx.align(
            transcription_result["segments"],
            align_model,
            align_metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        aligned_segments = alignment_result["segments"]

        return aligned_segments, detected_language

    finally:
        del alignment_result
        del transcription_result
        del audio
        gc.collect()

        if device == "cuda":
            torch.cuda.empty_cache()


# =============================================================================
# TRANSCRIPT PAR SCÈNE
# =============================================================================

def add_scenes_transcripts(
    scenes: List[Scene],
    whisper_segments: List[SingleAlignedSegment],
    use_words: bool = True,
    boundary_window: int = 15
) -> List[Scene]:

    scenes = sorted(scenes, key=lambda s: s["start_ms"])

    if not scenes:
        return scenes

    timed_units = []

    if use_words:
        for seg in whisper_segments:
            for w in seg.get("words", []) or []:
                text = (w.get("word") or "").strip()
                start = w.get("start")
                end = w.get("end")

                if not text or start is None or end is None:
                    continue

                timed_units.append({
                    "start_ms": int(float(start) * 1000),
                    "end_ms": int(float(end) * 1000),
                    "text": text,
                })

    if not timed_units:
        for seg in whisper_segments:
            text = (seg.get("text") or "").strip()
            start = seg.get("start")
            end = seg.get("end")

            if not text or start is None or end is None:
                continue

            timed_units.append({
                "start_ms": int(float(start) * 1000),
                "end_ms": int(float(end) * 1000),
                "text": text,
            })

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
        current_scene = scenes[i]
        next_scene = scenes[i + 1]

        current_scene_index = current_scene["index"]
        next_scene_index = next_scene["index"]

        current_indices = sorted(scene_token_indices[current_scene_index])
        next_indices = sorted(scene_token_indices[next_scene_index])

        if not current_indices or not next_indices:
            continue

        left_indices = current_indices[-boundary_window:]
        right_indices = next_indices[:boundary_window]
        pivot_idx = right_indices[0]

        left_pause_idx = None
        for idx in reversed(left_indices):
            token_text = timed_units[idx]["text"].strip()
            if pause_pattern.search(token_text):
                left_pause_idx = idx
                break

        right_pause_idx = None
        for idx in right_indices:
            token_text = timed_units[idx]["text"].strip()
            if pause_pattern.search(token_text):
                right_pause_idx = idx
                break

        cut_idx = None

        if left_pause_idx is not None and right_pause_idx is not None:
            left_distance = abs(left_pause_idx - pivot_idx)
            right_distance = abs(right_pause_idx - pivot_idx)

            if left_distance < right_distance:
                cut_idx = left_pause_idx
            elif right_distance < left_distance:
                cut_idx = right_pause_idx
            else:
                left_is_strong = bool(
                    strong_pause_pattern.search(timed_units[left_pause_idx]["text"].strip())
                )
                right_is_strong = bool(
                    strong_pause_pattern.search(timed_units[right_pause_idx]["text"].strip())
                )

                if right_is_strong and not left_is_strong:
                    cut_idx = right_pause_idx
                else:
                    cut_idx = left_pause_idx

        elif left_pause_idx is not None:
            cut_idx = left_pause_idx
        elif right_pause_idx is not None:
            cut_idx = right_pause_idx

        if cut_idx is None:
            continue

        affected_indices = left_indices + [idx for idx in right_indices if idx not in left_indices]

        for idx in affected_indices:
            if idx <= cut_idx:
                if idx in scene_token_indices[next_scene_index]:
                    scene_token_indices[next_scene_index].remove(idx)
                if idx not in scene_token_indices[current_scene_index]:
                    scene_token_indices[current_scene_index].append(idx)
            else:
                if idx in scene_token_indices[current_scene_index]:
                    scene_token_indices[current_scene_index].remove(idx)
                if idx not in scene_token_indices[next_scene_index]:
                    scene_token_indices[next_scene_index].append(idx)

    for scene in scenes:
        indices = sorted(
            scene_token_indices[scene["index"]],
            key=lambda idx: (timed_units[idx]["start_ms"], timed_units[idx]["end_ms"])
        )
        text = " ".join(timed_units[idx]["text"] for idx in indices).strip()
        scene["transcript"] = text

    return scenes
