from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

from watchagent.core.types import ClosedScene
from watchagent.perception.frame_sampler import FrameSampler
from watchagent.perception.scene_segmenter import OnlineSceneSegmenter
from watchagent.video_streaming.rtsp_streamer import RTSPFrameStreamer
from watchagent.vlm.scene_describer import SceneDescriber


SceneCallback = Callable[[ClosedScene, str], None]


class WatchAgent:
    """
    Agent principal de surveillance vidéo.

    Orchestre le pipeline complet :
      RTSP → FrameSampler → OnlineSceneSegmenter → SceneDescriber (VLM)

    La détection de scènes s'exécute dans le thread principal (temps réel).
    Les appels au VLM s'exécutent dans un pool de threads dédié afin de ne pas
    bloquer l'ingestion vidéo.

    Args:
        streamer:           Source vidéo RTSP.
        sampler:            Sous-échantillonneur temporel de frames.
        segmenter:          Détecteur de scènes en ligne.
        describer:          Descripteur VLM de scènes.
        on_scene_described: Callback appelé après chaque description VLM.
                            Signature : (scene: ClosedScene, description: str) -> None
        vlm_workers:        Nombre de threads parallèles pour les appels VLM.
                            1 suffit pour la plupart des setups locaux.
    """

    def __init__(
        self,
        streamer: RTSPFrameStreamer,
        sampler: FrameSampler,
        segmenter: OnlineSceneSegmenter,
        describer: SceneDescriber,
        on_scene_described: Optional[SceneCallback] = None,
        vlm_workers: int = 1,
    ) -> None:
        self.streamer = streamer
        self.sampler = sampler
        self.segmenter = segmenter
        self.describer = describer
        self.on_scene_described = on_scene_described
        self.vlm_workers = vlm_workers

        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: list[Future] = []
        self._futures_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Démarre l'agent et bloque jusqu'à la fin du flux ou KeyboardInterrupt.

        Le flux RTSP est lu en temps réel. Chaque scène fermée est soumise
        au VLM de façon asynchrone via le pool de threads interne.
        La méthode attend la fin de toutes les descriptions VLM avant de retourner.
        """
        self._executor = ThreadPoolExecutor(max_workers=self.vlm_workers)
        last_stream_status: Optional[str] = None

        try:
            self.streamer.start()

            for packet in self.streamer.frames():
                state = self.streamer.get_stream_state()
                if state["status"] != last_stream_status:
                    print(f"[STREAM] {state['status']}")
                    last_stream_status = state["status"]

                for sampled in self.sampler.add_frame(packet):
                    self._on_closed_scene(self.segmenter.update(sampled))

            # Flush du sampler (frames résiduelles en fin de flux)
            for sampled in self.sampler.flush():
                self._on_closed_scene(self.segmenter.update(sampled))

            # Flush du segmenteur (scène active non encore fermée)
            self._on_closed_scene(
                self.segmenter.flush(reason="CLOSED_BY_DISCONNECT")
            )

        except KeyboardInterrupt:
            print("\n[AGENT] Arrêt demandé par l'utilisateur.")

        finally:
            self._wait_for_vlm_tasks()
            self._executor.shutdown(wait=True)
            self.streamer.stop()
            print("[AGENT] Arrêt complet.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_closed_scene(self, scene: Optional[ClosedScene]) -> None:
        """Loggue et soumet une scène fermée au pool VLM (si non None)."""
        if scene is None:
            return

        print(
            f"[SCENE] index={scene['scene_index']:04d}  "
            f"status={scene['status']}  "
            f"frames={scene['frames_count']}  "
            f"keyframes={len(scene['selected_keyframes'])}"
        )

        if not scene["selected_keyframes"]:
            return

        with self._futures_lock:
            future = self._executor.submit(self._describe_scene, scene)  # type: ignore[union-attr]
            self._futures.append(future)

    def _describe_scene(self, scene: ClosedScene) -> None:
        """Appelle le VLM et déclenche le callback utilisateur."""
        try:
            description = self.describer.describe(scene)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[VLM ERROR] scene_index={scene['scene_index']}  "
                f"error={exc}"
            )
            return

        print(
            f"\n[VLM] scene_index={scene['scene_index']:04d}  "
            f"({scene['start_timestamp_utc']} → {scene['end_timestamp_utc']})\n"
            f"{description}\n"
            f"{'─' * 60}"
        )

        if self.on_scene_described is not None:
            try:
                self.on_scene_described(scene, description)
            except Exception as exc:  # noqa: BLE001
                print(f"[CALLBACK ERROR] scene_index={scene['scene_index']}  error={exc}")

    def _wait_for_vlm_tasks(self) -> None:
        """Attend la fin de toutes les tâches VLM en cours."""
        with self._futures_lock:
            futures = list(self._futures)

        pending = len(futures)
        if pending:
            print(f"[AGENT] Attente de {pending} description(s) VLM en cours...")

        for future in futures:
            try:
                future.result()
            except Exception:
                pass
