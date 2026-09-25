from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from watchagent.core.types import ClosedScene
from watchagent.settings import (
    DEFAULT_FPS,
    DESCRIBER_CONTEXT_WINDOW,
    DESCRIBER_MAX_KEYFRAMES,
)
from watchagent.vlm.ollama_client import OllamaVLMClient, PriorTurn


_SYSTEM_PROMPT = (
    "Tu es un système expert en analyse de vidéosurveillance. "
    "Tu analyses une séquence continue de scènes issues d'une même caméra. "
    "Tes descriptions doivent être factuelles, précises, concises et "
    "narrativement cohérentes avec les scènes précédentes lorsqu'un contexte est fourni. "
    "Si des éléments (personnes, véhicules, objets) apparaissent dans plusieurs scènes, "
    "fais-y référence pour assurer la continuité narrative."
)

# ── Contexte inter-scènes (scènes passées, sans images) ─────────────────────
_PRIOR_USER_MSG = (
    "Analyse la scène {scene_index:04d} de la caméra "
    "({duration:.1f} s, {n_keyframes} keyframe(s))."
)

# ── Batches intermédiaires (observation courte, pas de synthèse) ─────────────
_CHUNK_PARTIAL_PROMPT = """\
Voici {n_frames} image(s) clé(s) — segment {chunk_idx}/{n_chunks} \
de la scène {scene_index:04d} ({duration:.1f} s au total).
Décris brièvement et factuellement ce que tu observes dans CE segment \
(personnes, actions, objets). Pas de synthèse générale pour l'instant."""

# ── Dernier batch : synthèse complète ────────────────────────────────────────
_CHUNK_FINAL_PROMPT_WITH_CONTEXT = """\
Voici les dernières images — segment {chunk_idx}/{n_chunks} \
de la scène {scene_index:04d}.

En te basant sur TOUS les segments analysés et sur les scènes précédentes, \
fournis la description complète de cette scène en français :
1. **Contexte** : type de lieu, conditions d'éclairage
2. **Présence** : personnes, véhicules, objets remarquables
3. **Actions** : mouvements et comportements observés sur l'ensemble de la scène
4. **Événement principal** : résumé de l'activité centrale \
(relie-la aux scènes précédentes si pertinent)
5. **Alerte** : comportement inhabituel ou suspect (ou "Rien à signaler")

Sois factuel et concis."""

_CHUNK_FINAL_PROMPT_NO_CONTEXT = """\
Voici les dernières images — segment {chunk_idx}/{n_chunks} \
de la scène {scene_index:04d} ({duration:.1f} s).

En te basant sur TOUS les segments analysés, \
fournis la description complète de cette scène en français :
1. **Contexte** : type de lieu, conditions d'éclairage
2. **Présence** : personnes, véhicules, objets remarquables
3. **Actions** : mouvements et comportements observés sur l'ensemble de la scène
4. **Événement principal** : résumé de l'activité centrale
5. **Alerte** : comportement inhabituel ou suspect (ou "Rien à signaler")

Sois factuel et concis."""


class _HistoryEntry:
    __slots__ = ("scene_index", "status", "duration", "n_keyframes", "description")

    def __init__(
        self,
        scene_index: int,
        status: str,
        duration: float,
        n_keyframes: int,
        description: str,
    ) -> None:
        self.scene_index = scene_index
        self.status = status
        self.duration = duration
        self.n_keyframes = n_keyframes
        self.description = description


class SceneDescriber:
    """
    Génère une description textuelle d'une scène fermée à partir de ses keyframes.

    Si le nombre de keyframes dépasse ``max_keyframes`` (limite du modèle VLM),
    la scène est découpée en batches et traitée en plusieurs appels séquentiels :
      - Batches intermédiaires : observation courte, injectée comme contexte
      - Dernier batch : synthèse complète de toute la scène

    Avec ``context_window > 0``, les descriptions des ``k`` scènes précédentes sont
    également injectées comme tours user/assistant pour assurer la cohérence narrative.

    .. note::
        Pour garantir un contexte ordonné, utiliser ``vlm_workers=1`` dans ``WatchAgent``.
    """

    def __init__(
        self,
        client: OllamaVLMClient,
        default_fps: float = DEFAULT_FPS,
        max_keyframes: int = DESCRIBER_MAX_KEYFRAMES,
        context_window: int = DESCRIBER_CONTEXT_WINDOW,
    ) -> None:
        """
        Args:
            client:         Instance d'OllamaVLMClient configurée.
            default_fps:    FPS de secours si la scène ne porte pas l'info.
            max_keyframes:  Taille d'un batch d'images par appel VLM.
                            Limité par le modèle (qwen2.5vl:3b : max 4–5).
                            Les scènes avec plus de keyframes sont automatiquement
                            découpées en plusieurs appels séquentiels.
            context_window: Nombre de scènes précédentes injectées dans le contexte
                            (0 = comportement stateless, pas de mémoire).
        """
        self.client = client
        self.default_fps = default_fps
        self.max_keyframes = max_keyframes
        self.context_window = context_window

        self._history: deque[_HistoryEntry] = deque(maxlen=max(0, context_window))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def describe(self, scene: ClosedScene) -> str:
        """
        Décrit une scène fermée.

        Si les keyframes sont trop nombreuses pour le modèle, des appels séquentiels
        sont effectués automatiquement — un par batch de ``max_keyframes`` images.

        Returns:
            Description finale de la scène (synthèse du ou des batch(es)).
        """
        keyframes = scene["selected_keyframes"]
        if not keyframes:
            return "[Aucun keyframe disponible pour cette scène]"

        frames = [frame for _, frame in keyframes]
        n_frames = len(frames)

        fps = scene.get("fps") or self.default_fps
        duration = (scene["end_frame"] - scene["start_frame"]) / fps
        scene_index = scene["scene_index"]

        n_chunks = max(1, (n_frames + self.max_keyframes - 1) // self.max_keyframes)
        max_res = self.client.max_resolution
        h, w = frames[0].shape[:2]
        eff_w = min(w, max_res) if max_res > 0 else w
        eff_h = int(h * eff_w / w) if w > 0 else h
        print(
            f"[VLM] scene={scene_index:04d}  kf={n_frames}  "
            f"chunks={n_chunks}×{self.max_keyframes}  "
            f"res={w}×{h}→{eff_w}×{eff_h}  "
            f"dur={duration:.1f}s  ctx={len(self._history)}"
        )

        with self._lock:
            prior_scene_turns = self._build_prior_turns()

        description = self._describe_in_chunks(
            frames, scene_index, duration, n_chunks, prior_scene_turns
        )

        with self._lock:
            self._history.append(
                _HistoryEntry(
                    scene_index=scene_index,
                    status=str(scene["status"]),
                    duration=duration,
                    n_keyframes=n_frames,
                    description=description,
                )
            )

        return description

    def clear_history(self) -> None:
        """Réinitialise l'historique de contexte (utile après reconnexion)."""
        with self._lock:
            self._history.clear()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _describe_in_chunks(
        self,
        frames: list[np.ndarray],
        scene_index: int,
        duration: float,
        n_chunks: int,
        prior_scene_turns: list[PriorTurn],
    ) -> str:
        """
        Découpe ``frames`` en batches de ``max_keyframes`` et effectue un appel
        VLM par batch dans l'ordre chronologique.

        Les réponses des batches intermédiaires sont injectées comme tours
        user/assistant dans le contexte des batches suivants (contexte intra-scène),
        en plus du contexte inter-scènes (``prior_scene_turns``).

        Le dernier batch reçoit un prompt de synthèse et retourne la description
        finale de la scène.
        """
        has_prior = len(prior_scene_turns) > 0
        intra_turns: list[PriorTurn] = []
        description = ""

        for chunk_idx, chunk_start in enumerate(
            range(0, len(frames), self.max_keyframes), start=1
        ):
            chunk = frames[chunk_start: chunk_start + self.max_keyframes]
            is_last = (chunk_idx == n_chunks)

            if is_last:
                prompt_tpl = (
                    _CHUNK_FINAL_PROMPT_WITH_CONTEXT
                    if (has_prior or intra_turns)
                    else _CHUNK_FINAL_PROMPT_NO_CONTEXT
                )
                prompt = prompt_tpl.format(
                    chunk_idx=chunk_idx,
                    n_chunks=n_chunks,
                    scene_index=scene_index,
                    duration=duration,
                    n_frames=len(chunk),
                )
            else:
                prompt = _CHUNK_PARTIAL_PROMPT.format(
                    chunk_idx=chunk_idx,
                    n_chunks=n_chunks,
                    scene_index=scene_index,
                    duration=duration,
                    n_frames=len(chunk),
                )

            t0 = time.time()
            description = self.client.describe_scene(
                chunk,
                prompt,
                system=_SYSTEM_PROMPT,
                prior_turns=prior_scene_turns + intra_turns or None,
            )
            print(
                f"[VLM] scene={scene_index:04d}  "
                f"chunk={chunk_idx}/{n_chunks}  {time.time()-t0:.1f}s"
            )

            if not is_last:
                intra_turns.append((prompt, description))

        return description

    def _build_prior_turns(self) -> list[PriorTurn]:
        """
        Construit les tours (user, assistant) pour les scènes de l'historique.
        Appelé sous ``_lock``.
        """
        turns: list[PriorTurn] = []
        for entry in self._history:
            user_msg = _PRIOR_USER_MSG.format(
                scene_index=entry.scene_index,
                duration=entry.duration,
                n_keyframes=entry.n_keyframes,
            )
            turns.append((user_msg, entry.description))
        return turns
