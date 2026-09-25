import time
import cv2
import threading
from queue import Empty, Full, Queue
from typing import Generator, Optional

from watchagent.core import FramePacket, StreamState, utc_now_iso
from watchagent.settings import (
    RTSP_FRAME_TIMEOUT,
    RTSP_MAX_RECONNECT_ATTEMPTS,
    RTSP_QUEUE_SIZE,
    RTSP_RECONNECT_INTERVAL,
    RTSP_STALL_TIMEOUT,
)


class RTSPFrameStreamer:
    def __init__(
        self,
        rtsp_url: str,
        queue_size: int = RTSP_QUEUE_SIZE,
        stall_timeout_seconds: float = RTSP_STALL_TIMEOUT,
        reconnect_interval_seconds: float = RTSP_RECONNECT_INTERVAL,
        max_reconnect_attempts: int = RTSP_MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self.rtsp_url = rtsp_url
        self.queue: Queue[FramePacket] = Queue(maxsize=queue_size)

        self.stall_timeout_seconds = stall_timeout_seconds
        self.reconnect_interval_seconds = reconnect_interval_seconds
        self.max_reconnect_attempts = max_reconnect_attempts

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None

        self._frame_index = 0
        self._fps: Optional[float] = None
        self._is_started = False
        self._last_frame_ts: Optional[float] = None
        self._current_reconnect_attempts = 0

        self.stream_state: StreamState = {
            "status": "DISCONNECTED",
            "last_frame_at_utc": None,
            "reconnect_count": 0,
            "stall_count": 0,
        }

    def start(self) -> None:
        if self._is_started:
            return

        try:
            self._open_capture()
        except RuntimeError:
            self._set_reconnecting()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()
        self._is_started = True

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)

        if self._cap is not None:
            self._cap.release()

        self._is_started = False

    def get_stream_state(self) -> StreamState:
        return self.stream_state.copy()

    def is_stalled(self) -> bool:
        if self._last_frame_ts is None:
            return False
        return (time.time() - self._last_frame_ts) >= self.stall_timeout_seconds

    def _open_capture(self) -> None:
        if self._cap is not None:
            self._cap.release()

        self._cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)

        if not self._cap.isOpened():
            raise RuntimeError(f"Impossible d'ouvrir le flux RTSP: {self.rtsp_url}")

        fps = self._cap.get(cv2.CAP_PROP_FPS)
        self._fps = fps if fps and fps > 0 else None

        self._current_reconnect_attempts = 0
        self.stream_state["status"] = "STREAMING"

    def _set_streaming(self) -> None:
        self.stream_state["status"] = "STREAMING"
        self.stream_state["last_frame_at_utc"] = utc_now_iso()
        self._last_frame_ts = time.time()
        self._current_reconnect_attempts = 0

    def _set_stalled(self) -> None:
        if self.stream_state["status"] in ("DISCONNECTED", "RECONNECTING"):
            return
        if self.stream_state["status"] != "STALLED":
            self.stream_state["stall_count"] += 1
        self.stream_state["status"] = "STALLED"

    def _set_reconnecting(self) -> None:
        self.stream_state["status"] = "RECONNECTING"
        self.stream_state["reconnect_count"] += 1

    def _set_disconnected(self) -> None:
        self.stream_state["status"] = "DISCONNECTED"

    def _try_reconnect(self) -> bool:
        if self._current_reconnect_attempts >= self.max_reconnect_attempts:
            self._set_disconnected()
            return False

        self._set_reconnecting()
        self._current_reconnect_attempts += 1
        time.sleep(self.reconnect_interval_seconds)

        try:
            self._open_capture()
            return True
        except RuntimeError:
            if self._current_reconnect_attempts >= self.max_reconnect_attempts:
                self._set_disconnected()
            return False

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.stream_state["status"] == "DISCONNECTED":
                break

            if self._cap is None or not self._cap.isOpened():
                ok = self._try_reconnect()
                if not ok:
                    continue

            ret, frame = self._cap.read()

            if not ret:
                if self.is_stalled():
                    self._set_stalled()

                ok = self._try_reconnect()
                if not ok:
                    continue
                else:
                    continue

            self._frame_index += 1
            height, width = frame.shape[:2]

            packet: FramePacket = {
                "frame_index": self._frame_index,
                "timestamp_utc": utc_now_iso(),
                "frame": frame,
                "fps": self._fps,
                "width": width,
                "height": height,
            }

            self._set_streaming()

            try:
                self.queue.put_nowait(packet)
            except Full:
                try:
                    _ = self.queue.get_nowait()
                except Empty:
                    pass

                try:
                    self.queue.put_nowait(packet)
                except Full:
                    pass

    def frames(self, timeout: float = RTSP_FRAME_TIMEOUT):
        if not self._is_started:
            raise RuntimeError("Le streamer n'est pas démarré. Appelle start() d'abord.")

        while not self._stop_event.is_set():
            if self.stream_state["status"] == "DISCONNECTED":
                break

            try:
                packet = self.queue.get(timeout=timeout)
                yield packet

            except Empty:
                # Toujours vérifier d'abord si on est terminal
                if self.stream_state["status"] == "DISCONNECTED":
                    break

                # Ne passer en STALLED que si le flux n'est pas déjà en reconnect/disconnect
                if (
                    self.stream_state["status"] == "STREAMING"
                    and self.is_stalled()
                ):
                    self._set_stalled()

                continue