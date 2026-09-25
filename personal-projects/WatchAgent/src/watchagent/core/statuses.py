from typing import Literal

StreamStatus = Literal[
    "STREAMING",
    "STALLED",
    "DISCONNECTED",
    "RECONNECTING",
]

SceneStatus = Literal[
    "ACTIVE",
    "CLOSED_BY_CHANGE",
    "CLOSED_BY_IDLE_TIMEOUT",
    "CLOSED_BY_DISCONNECT",
]