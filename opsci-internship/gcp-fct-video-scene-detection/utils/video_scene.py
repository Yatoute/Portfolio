from typing import TypedDict, List, Tuple

import cv2

from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from scenedetect.scene_detector import FlashFilter


class Scene(TypedDict):
    index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    transcript: str

def get_video_metadata(video_path: str) -> dict:
    """Retourne fps, frame_count, width, height, duration."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Impossible d'ouvrir : {video_path}")
    fps         = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    fps = fps if fps > 0 else 25.0
    return {
        "fps": float(fps),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration": frame_count / fps,
    }


def detect_scenes(
    video_path: str,
    threshold: float = 30.0,
    min_scene_duration: float = 10.0
) -> Tuple[List[Scene], dict]:
    """
    Détecte les scènes d'une vidéo via PySceneDetect (ContentDetector).
    """

    metadata = get_video_metadata(video_path)
    fps = metadata["fps"]
    min_scene_len_frames = max(1, int(round(fps * min_scene_duration)))

    video_sd = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(
        ContentDetector(
            threshold=threshold,
            min_scene_len=min_scene_len_frames,
            filter_mode=FlashFilter.Mode.SUPPRESS,
        )
    )
    scene_manager.detect_scenes(video_sd)

    scenes = []
    for idx, (start, end) in enumerate(scene_manager.get_scene_list()):
        scenes.append(
            Scene(
                index=idx,
                start_frame=start.get_frames(),
                end_frame=end.get_frames(),
                start_ms=int(start.get_seconds() * 1000),
                end_ms=int(end.get_seconds() * 1000),
                transcript=""
            )
        )

    if not scenes:
        duration = metadata["duration"]
        scenes = [
            Scene(
                index=0,
                start_frame=0,
                end_frame=metadata["frame_count"],
                start_ms=0,
                end_ms=int(duration * 1000),
                transcript=""
            )
        ]

    return scenes, metadata
