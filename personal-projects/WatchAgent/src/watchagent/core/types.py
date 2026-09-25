from typing import Any, Optional, TypedDict
from collections import deque

import numpy as np

from watchagent.core.statuses import SceneStatus, StreamStatus


class FramePacket(TypedDict):
    frame_index: int
    timestamp_utc: str
    frame: np.ndarray
    fps: Optional[float]
    width: int
    height: int


class SceneState(TypedDict):
    scene_id: str
    scene_index: int
    status: SceneStatus

    start_frame: int
    start_timestamp_utc: str
    fps: Optional[float]

    frames: list[tuple[int, np.ndarray]]

    reference_hist: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]
    previous_hist: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]

    reference_lowres: Optional[np.ndarray]
    previous_lowres: Optional[np.ndarray]

    consecutive_change_count: int
    frames_features: list[dict[str, Any]]


class ClosedScene(TypedDict):
    scene_id: str
    scene_index: int
    status: SceneStatus

    start_frame: int
    end_frame: int

    start_timestamp_utc: str
    end_timestamp_utc: str

    fps: Optional[float]
    frames_count: int
    selected_keyframes: list[tuple[int, np.ndarray]]

    transition_reason: str
    


class StreamState(TypedDict):
    status: StreamStatus
    last_frame_at_utc: Optional[str]
    reconnect_count: int
    stall_count: int