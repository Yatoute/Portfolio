from typing import Optional

from watchagent.core import FramePacket
from watchagent.perception.stable_frame_selector import add_temporal_stability_scores
from watchagent.settings import (
    DEFAULT_SOURCE_FPS,
    SAMPLER_EDGE_MARGIN_RATIO,
    SAMPLER_TARGET_FPS,
)


class FrameSampler:
    """
    Echantillonner les frames avant la détection de scènes.

    Objectif :
    - bufferiser environ 1 seconde de frames
    - sélectionner `target_fps` frames représentatives
    - respecter une couverture temporelle homogène
    - privilégier les frames stables dans chaque sous-intervalle
    """

    def __init__(
        self,
        source_fps: float = DEFAULT_SOURCE_FPS,
        target_fps: float = SAMPLER_TARGET_FPS,
        edge_margin_ratio: float = SAMPLER_EDGE_MARGIN_RATIO,
    ) -> None:
        self.source_fps = source_fps if source_fps > 0 else 30.0
        self.target_fps = target_fps if target_fps > 0 else 5.0
        self.edge_margin_ratio = edge_margin_ratio

        self.window_size_frames = max(1, int(round(self.source_fps)))
        self._buffer: list[FramePacket] = []

    def add_frame(self, packet: FramePacket) -> list[FramePacket]:
        """
        Ajoute une frame au buffer.

        Retourne :
        - [] si la fenêtre de 1 seconde n'est pas encore complète
        - une liste de frames sélectionnées sinon
        """
        self._buffer.append(packet)

        if len(self._buffer) < self.window_size_frames:
            return []

        selected = self._select_from_buffer(self._buffer)
        self._buffer = []
        return selected

    def flush(self) -> list[FramePacket]:
        """
        Permet de traiter le reliquat en fin de flux.
        """
        if not self._buffer:
            return []

        selected = self._select_from_buffer(self._buffer)
        self._buffer = []
        return selected

    def _select_from_buffer(self, packets: list[FramePacket]) -> list[FramePacket]:
        if not packets:
            return []

        if len(packets) <= 2:
            return packets

        indexed_frames = [
            (packet["frame_index"], packet["frame"])
            for packet in packets
        ]

        n_frames = len(indexed_frames)

        edge_margin = int(round(n_frames * self.edge_margin_ratio))
        if 2 * edge_margin >= n_frames:
            edge_margin = 0

        candidate_packets = (
            packets[edge_margin:n_frames - edge_margin]
            if edge_margin > 0
            else packets
        )

        if len(candidate_packets) <= 2:
            candidate_packets = packets

        candidate_indexed_frames = [
            (packet["frame_index"], packet["frame"])
            for packet in candidate_packets
        ]

        scored = add_temporal_stability_scores(candidate_indexed_frames)

        score_map = {
            abs_idx: score
            for abs_idx, _, score in scored
        }

        slots_per_second = max(1, int(round(self.target_fps)))

        if len(candidate_packets) <= slots_per_second:
            return candidate_packets

        slot_size = len(candidate_packets) / slots_per_second
        selected_packets: list[FramePacket] = []

        for slot_idx in range(slots_per_second):
            sub_start = int(round(slot_idx * slot_size))
            sub_end = int(round((slot_idx + 1) * slot_size))

            sub_packets = candidate_packets[sub_start:sub_end]
            if not sub_packets:
                continue

            best_packet: Optional[FramePacket] = None
            best_score: Optional[float] = None

            for packet in sub_packets:
                score = score_map.get(packet["frame_index"], 0.0)

                if best_score is None or score < best_score:
                    best_packet = packet
                    best_score = score

            if best_packet is not None:
                selected_packets.append(best_packet)

        selected_packets.sort(key=lambda p: p["frame_index"])
        return selected_packets