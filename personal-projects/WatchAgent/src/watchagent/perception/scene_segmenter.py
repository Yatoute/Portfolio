from __future__ import annotations

from collections import deque
from typing import Optional, List, Tuple

import numpy as np
import uuid

from watchagent.core import ClosedScene, FramePacket, SceneState, utc_now_iso
from watchagent.perception.scene_features import (
    bhattacharyya_distance,
    compute_normalized_histogram,
    preprocess_frame,
    to_lowres_grayscale,
)

from watchagent.settings import (
    DEFAULT_FPS,
    LOWRES_SIZE,
    SCENE_FRAME_SIMILARITY_THRESHOLD,
    SCENE_GLOBAL_CHANGE_THRESHOLD,
    SCENE_LOCAL_CHANGE_THRESHOLD,
    SCENE_MIN_LEN_FRAMES,
)


class OnlineSceneSegmenter:
    def __init__(
        self,
        scene_global_change_threshold: float = SCENE_GLOBAL_CHANGE_THRESHOLD,
        scene_local_change_threshold: float = SCENE_LOCAL_CHANGE_THRESHOLD,
        frame_similarity_threshold: float = SCENE_FRAME_SIMILARITY_THRESHOLD,
        min_scene_len_frames: int = SCENE_MIN_LEN_FRAMES,
        max_scene_buffer_frames: Optional[int] = None,
        lowres_size: tuple[int, int] = LOWRES_SIZE,
        default_fps: float = DEFAULT_FPS,
    ) -> None:
        self.scene_global_change_threshold = scene_global_change_threshold
        self.scene_local_change_threshold = scene_local_change_threshold
        self.frame_similarity_threshold = frame_similarity_threshold
        self.min_scene_len_frames = min_scene_len_frames
        self.max_scene_buffer_frames = max_scene_buffer_frames
        self.lowres_size = lowres_size
        self.default_fps = default_fps

        self.current_scene: Optional[SceneState] = None
        self.scene_counter = 0
    
    def select_keyframes(self) -> List[Tuple[int, np.ndarray]]:
        
        scene = self.current_scene
        if scene is None:
            return []
        
        frames_features = scene["frames_features"]
        frames = scene["frames"]
        
        keyframes = []
        for frames, features in zip(frames, frames_features):
            
            is_similar_to_previous = features["is_similar_to_previous"]
            if not is_similar_to_previous:
                keyframes.append(frames)
                
        return keyframes
        
        

    def update(self, packet: FramePacket) -> Optional[ClosedScene]:
        frame = packet["frame"]

        current_hist = compute_normalized_histogram(preprocess_frame(frame))
        current_lowres = to_lowres_grayscale(frame, size=self.lowres_size)

        if self.current_scene is None:
            self.current_scene = self._start_scene(packet, current_hist, current_lowres)
            return None

        scene = self.current_scene

        prev_hist_distance = (
            bhattacharyya_distance(current_hist, scene["previous_hist"])
            if scene["previous_hist"] is not None
            else 0.0
        )

        ref_hist_distance = (
            bhattacharyya_distance(current_hist, scene["reference_hist"])
            if scene["reference_hist"] is not None
            else 0.0
        )

        is_similar_to_previous = prev_hist_distance < self.frame_similarity_threshold

        is_end_of_scene = (
            ref_hist_distance >= self.scene_global_change_threshold
            and prev_hist_distance >= self.scene_local_change_threshold
            and len(scene["frames"]) >= self.min_scene_len_frames
        )
        

        scene["frames_features"].append(
            {
                "frame_index": packet["frame_index"],
                "is_end_of_scene": is_end_of_scene,
                "is_similar_to_previous": is_similar_to_previous,
            }
        )

        if is_end_of_scene:
            closed_scene = self._close_scene(packet, "CLOSED_BY_CHANGE", "confirmed_visual_change")
            self.current_scene = self._start_scene(packet, current_hist, current_lowres)
            return closed_scene

        scene["frames"].append((packet["frame_index"], frame.copy()))

        if self.max_scene_buffer_frames is not None:
            scene["frames"] = scene["frames"][-self.max_scene_buffer_frames :]

        scene["previous_hist"] = current_hist
        scene["previous_lowres"] = current_lowres

        return None

    def flush(self, reason: str = "CLOSED_BY_DISCONNECT") -> Optional[ClosedScene]:
        if self.current_scene is None or len(self.current_scene["frames"]) == 0:
            return None

        status = reason
        transition_reason = reason.lower()

        scene = self.current_scene
        
        closed_scene: ClosedScene = {
            "scene_id": scene["scene_id"],
            "scene_index": scene["scene_index"],
            "status": status,
            "start_frame": scene["start_frame"],
            "end_frame": scene["frames"][-1][0],
            "start_timestamp_utc": scene["start_timestamp_utc"],
            "end_timestamp_utc": utc_now_iso(),
            "fps": scene.get("fps"),
            "frames_count": len(scene["frames"]),
            "selected_keyframes": self.select_keyframes(),
            "transition_reason": transition_reason,
        }

        self.current_scene = None
        return closed_scene

    def _start_scene(
        self,
        packet: FramePacket,
        current_hist,
        current_lowres,
    ) -> SceneState:
        scene: SceneState = {
            "scene_id": str(uuid.uuid4()),
            "scene_index": self.scene_counter,
            "status": "ACTIVE",
            "start_frame": packet["frame_index"],
            "start_timestamp_utc": packet["timestamp_utc"],
            "fps": packet["fps"],
            "frames": [(packet["frame_index"], packet["frame"].copy())],
            "reference_hist": current_hist,
            "previous_hist": current_hist,
            "reference_lowres": current_lowres,
            "previous_lowres": current_lowres,
            "consecutive_change_count": 0,
            "frames_features": [
                {
                    "frame_index": packet["frame_index"],
                    "is_end_of_scene": False,
                    "is_similar_to_previous": False,
                }
            ],
        }

        self.scene_counter += 1
        return scene

    def _close_scene(
        self,
        packet: FramePacket,
        status: str,
        transition_reason: str,
    ) -> ClosedScene:
        scene = self.current_scene
        assert scene is not None

        return {
            "scene_id": scene["scene_id"],
            "scene_index": scene["scene_index"],
            "status": status,
            "start_frame": scene["start_frame"],
            "end_frame": scene["frames"][-1][0],
            "start_timestamp_utc": scene["start_timestamp_utc"],
            "end_timestamp_utc": packet["timestamp_utc"],
            "fps": scene.get("fps"),
            "frames_count": len(scene["frames"]),
            "selected_keyframes": self.select_keyframes(),
            "transition_reason": transition_reason,
        }

    def _resolve_scene_fps(self, scene: SceneState) -> float:
        if scene.get("fps") is not None:
            return scene["fps"] or self.default_fps
        return self.default_fps