import logging
import math
from typing import Optional, Iterator, TypedDict

import cv2
import numpy as np
import torch
import open_clip
from PIL import Image as PILImage


# =============================================================================
# TYPES
# =============================================================================

class Scene(TypedDict):
    index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    transcript: Optional[str]


# =============================================================================
# LECTURE EN STREAMING DES FRAMES
# =============================================================================

def stream_scene_frames(
    cap: cv2.VideoCapture,
    scene: Scene,
    max_frames_to_decode: int = 0,
) -> Iterator[tuple[int, np.ndarray]]:
    """Génère les frames d'une scène en sous-échantillonnant si nécessaire.

    Si max_frames_to_decode > 0 et que la scène dépasse ce seuil, saute les frames
    intermédiaires avec cap.grab() (lecture sans décodage) pour limiter le coût CPU.
    """
    start_frame = int(scene["start_frame"])
    end_frame = int(scene["end_frame"])
    n_frames = end_frame - start_frame

    if n_frames <= 0:
        return

    step = max(1, n_frames // max_frames_to_decode) if max_frames_to_decode > 0 else 1

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    current_frame_idx = start_frame

    while current_frame_idx < end_frame:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        yield (current_frame_idx, frame)
        ## Sauter step-1 frames sans les décoder (cap.grab ≈ 10× plus rapide que cap.read)
        for _ in range(step - 1):
            if not cap.grab():
                return
        current_frame_idx += step


def _to_lowres_grayscale(frame: np.ndarray, size: tuple[int, int] = (96, 54)) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)


def _frame_change_score(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(frame_a, frame_b)))


def _yield_most_stable_from_window(
    window: list[tuple[int, np.ndarray]],
    n_to_keep: int,
) -> Iterator[tuple[int, np.ndarray]]:
    """Sélectionne les n_to_keep frames les plus stables d'une fenêtre temporelle.

    La stabilité est mesurée en basse résolution (96×54 niveaux de gris) comme
    la moyenne des changements avec les frames voisines. Les lowres sont libérées
    après scoring. Les frames retenues sont yielded en ordre temporel.
    """
    if len(window) <= n_to_keep:
        yield from window
        return

    lowres = [_to_lowres_grayscale(frame) for _, frame in window]

    scored: list[tuple[int, np.ndarray, float]] = []
    for i, (frame_idx, frame) in enumerate(window):
        if i == 0:
            score = _frame_change_score(lowres[i], lowres[i + 1])
        elif i == len(window) - 1:
            score = _frame_change_score(lowres[i], lowres[i - 1])
        else:
            score = (_frame_change_score(lowres[i], lowres[i - 1])
                     + _frame_change_score(lowres[i], lowres[i + 1])) / 2.0
        scored.append((frame_idx, frame, score))

    ## Libération explicite des lowres après scoring
    del lowres

    ## Les frames avec le score le plus bas sont les plus stables (moins de changement avec leurs voisines)
    scored.sort(key=lambda x: x[2])
    selected = scored[:n_to_keep]
    selected.sort(key=lambda x: x[0])

    yield from ((frame_idx, frame) for frame_idx, frame, _ in selected)


def stream_locally_stable_frames(
    frames_stream: Iterator[tuple[int, np.ndarray]],
    fps: float,
    target_fps: float = 15.0,
) -> Iterator[tuple[int, np.ndarray]]:
    """Sous-échantillonne le flux en sélectionnant les frames les plus stables par fenêtre.

    Découpe le flux en fenêtres de `fps` frames (~1 seconde) et retient les
    `target_fps` frames les plus stables de chaque fenêtre. Garantit une
    couverture temporelle uniforme tout en évitant les frames floues ou en mouvement.

    Au plus `fps` frames (~1 seconde de vidéo) coexistent en mémoire à la fois.
    """
    window_size = max(1, int(round(fps)))
    n_to_keep = max(1, int(round(target_fps)))
    window: list[tuple[int, np.ndarray]] = []

    for frame_idx, frame in frames_stream:
        window.append((frame_idx, frame))

        if len(window) >= window_size:
            yield from _yield_most_stable_from_window(window, n_to_keep)
            window = []

    ## Fenêtre finale (partielle) : conserver la même proportion que les fenêtres complètes
    if window:
        n_remaining = max(1, round(len(window) * target_fps / fps))
        yield from _yield_most_stable_from_window(window, n_remaining)


# =============================================================================
# HISTOGRAMMES
# =============================================================================

def preprocess_frame(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)


def compute_normalized_histogram(
    hsv_frame: np.ndarray,
    h_bins: int = 50,
    s_bins: int = 60,
    v_bins: int = 50,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hist_h = cv2.calcHist([hsv_frame], [0], None, [h_bins], [0, 180]).astype(np.float32)
    hist_s = cv2.calcHist([hsv_frame], [1], None, [s_bins], [0, 256]).astype(np.float32)
    hist_v = cv2.calcHist([hsv_frame], [2], None, [v_bins], [0, 256]).astype(np.float32)

    hist_h /= (hist_h.sum() + 1e-12)
    hist_s /= (hist_s.sum() + 1e-12)
    hist_v /= (hist_v.sum() + 1e-12)

    return hist_h, hist_s, hist_v


def bhattacharyya_distance(hists1: tuple, hists2: tuple) -> float:
    d_h = cv2.compareHist(hists1[0], hists2[0], cv2.HISTCMP_BHATTACHARYYA)
    d_s = cv2.compareHist(hists1[1], hists2[1], cv2.HISTCMP_BHATTACHARYYA)
    d_v = cv2.compareHist(hists1[2], hists2[2], cv2.HISTCMP_BHATTACHARYYA)
    return (d_h + d_s + d_v) / 3.0


def sharpness_score(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def select_sharpest_frame(indexed_frames: list[tuple[int, np.ndarray]]) -> tuple[int, np.ndarray]:
    return max(indexed_frames, key=lambda x: sharpness_score(x[1]))


def collect_visually_distinct_frames(
    frames_stream: Iterator[tuple[int, np.ndarray]],
    visually_distinct_frame_threshold: float = 0.15,
    max_candidates: int = 10,
) -> tuple[list[tuple[int, np.ndarray, tuple]], float, int]:
    """Consomme le flux et retourne les frames visuellement les plus distinctes avec leurs histogrammes.

    Maintient un ensemble borné à max_candidates frames via seuil dynamique :
    - Quand max_candidates est atteint et qu'une nouvelle frame dépasse le seuil,
      la frame avec la plus petite distance Bhattacharyya est évincée et libérée.
    - Le seuil monte alors au niveau de la distance minimale du nouvel ensemble,
      ce qui filtre progressivement les transitions faibles sur les scènes très denses.
    - Borne la mémoire à max_candidates frames full-res quel que soit la densité visuelle.
    - Fallback sur la frame la plus nette si aucune transition n'est détectée.

    Returns:
        candidates    : frames visuellement distinctes — (frame_idx, frame, hists)
        scene_density : distance de Bhattacharyya moyenne ∈ [0, 1]
        n_comparisons : nombre de paires de frames comparées
    """
    prev_idx: Optional[int] = None
    prev_frame: Optional[np.ndarray] = None
    prev_hists = None

    sharpest: Optional[tuple[int, np.ndarray, tuple]] = None
    sharpest_score_val = -1.0

    ## Candidats : (frame_idx, frame, dist, hists) — dist sert à l'éviction et au seuil dynamique
    candidates: list[tuple[int, np.ndarray, float, tuple]] = []
    dynamic_threshold = visually_distinct_frame_threshold

    total_distance = 0.0
    n_comparisons = 0

    for frame_idx, frame in frames_stream:
        hsv = preprocess_frame(frame)
        curr_hists = compute_normalized_histogram(hsv)

        if prev_hists is None:
            sharpest_score_val = sharpness_score(frame)
            sharpest = (frame_idx, frame, curr_hists)
            prev_idx, prev_frame, prev_hists = frame_idx, frame, curr_hists
            continue

        dist = bhattacharyya_distance(prev_hists, curr_hists)
        total_distance += dist
        n_comparisons += 1

        if dist > dynamic_threshold:
            ## Première transition : inclure aussi le prev_frame comme ancre temporelle
            if not candidates:
                candidates.append((prev_idx, prev_frame, dist, prev_hists))
            candidates.append((frame_idx, frame, dist, curr_hists))

            ## Éviction du candidat le plus faible tant qu'on dépasse le cap
            while len(candidates) > max_candidates:
                min_idx = min(range(len(candidates)), key=lambda i: candidates[i][2])
                candidates.pop(min_idx)  # libère la frame évincée (CPython refcounting)

            ## Seuil dynamique = distance minimale dans l'ensemble courant
            if len(candidates) >= max_candidates:
                dynamic_threshold = min(c[2] for c in candidates)

        elif not candidates:
            ## Pas encore de transition : tracker la frame la plus nette (fallback)
            sharp = sharpness_score(frame)
            if sharp > sharpest_score_val:
                sharpest_score_val = sharp
                sharpest = (frame_idx, frame, curr_hists)

        prev_idx, prev_frame, prev_hists = frame_idx, frame, curr_hists

    scene_density = min(1.0, total_distance / n_comparisons) if n_comparisons > 0 else 0.0

    result = [(idx, frm, hists) for idx, frm, _, hists in candidates]

    ## Fallback : aucune transition détectée → frame la plus nette
    if not result:
        result = [sharpest] if sharpest is not None else []

    return result, scene_density, n_comparisons


# =============================================================================
# CLIP
# =============================================================================

class CLIPTextFrameSelector:
    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = "openai",
        device: str = "cpu",
    ):
        self.device = torch.device(device)
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
        )
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.model.eval().to(self.device)

    @torch.no_grad()
    def encode_text(self, text: str) -> np.ndarray:
        tokens = self.tokenizer([text]).to(self.device)
        feat = self.model.encode_text(tokens)
        feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat[0].detach().cpu().numpy().astype(np.float32)

    @torch.no_grad()
    def encode_frame(self, bgr_frame: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        pil = PILImage.fromarray(rgb)
        tensor = self.preprocess(pil).unsqueeze(0).to(self.device)
        feat = self.model.encode_image(tensor)
        feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat[0].detach().cpu().numpy().astype(np.float32)

    @torch.no_grad()
    def encode_frames_batch(self, bgr_frames: list) -> list:
        """Encode un batch de frames en une seule passe forward — évite N appels séquentiels à encode_frame."""
        if not bgr_frames:
            return []
        tensors = []
        for frame in bgr_frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil = PILImage.fromarray(rgb)
            tensors.append(self.preprocess(pil))
        batch = torch.stack(tensors).to(self.device)
        clip_vectors = self.model.encode_image(batch)
        clip_vectors = clip_vectors / clip_vectors.norm(dim=-1, keepdim=True)
        del batch
        return [f.detach().cpu().numpy().astype(np.float32) for f in clip_vectors]

    @staticmethod
    def cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        return float(np.dot(v1, v2))


# =============================================================================
# PIPELINE SCÈNE
# =============================================================================

# Candidat type : (frame_idx, frame, hists, clip_vec)
# hists  : tuple (hist_h, hist_s, hist_v) — toujours calculé pour la dédup globale
# clip_vec : np.ndarray L2-normalisé ou None si use_clip=False
SceneCandidate = tuple[int, np.ndarray, tuple, Optional[np.ndarray]]


def compute_scene_data(
    cap: cv2.VideoCapture,
    scene: Scene,
    visually_distinct_frame_threshold: float = 0.10,
    use_histo: bool = True,
    use_clip: bool = True,
    clip_selector: Optional[CLIPTextFrameSelector] = None,
    use_stability_filter: bool = True,
    target_fps: float = 3.0,
    max_candidates: int = 10,
    max_frames_to_decode: int = 1000,
) -> tuple[list[SceneCandidate], Optional[np.ndarray]]:
    """
    Collecte les candidats visuels d'une scène et calcule l'embedding de scène.

    Les histogrammes HSV sont toujours calculés (utilisés pour la déduplication globale).
    L'embedding de scène est la moyenne L2-normalisée des vecteurs CLIP de tous les candidats.
    Aucune keyframe n'est sélectionnée ni uploadée ici — c'est fait globalement après toutes
    les scènes via select_video_keyframes().

    Returns:
        candidates      : liste de (frame_idx, frame, hists, clip_vec) à verser dans le pool vidéo
        scene_embedding : vecteur CLIP L2-normalisé, None si clip_selector absent
    """
    fps_video = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_scene_frames = int(scene["end_frame"]) - int(scene["start_frame"])
    decode_step = max(1, n_scene_frames // max_frames_to_decode) if max_frames_to_decode > 0 else 1

    frames_stream = stream_scene_frames(cap, scene, max_frames_to_decode=max_frames_to_decode)

    if use_stability_filter and decode_step == 1:
        frames_stream = stream_locally_stable_frames(frames_stream, fps=fps_video, target_fps=target_fps)

    ## collect_visually_distinct_frames retourne déjà les histogrammes — pas de double calcul
    if use_histo:
        raw_candidates, _, _ = collect_visually_distinct_frames(
            frames_stream,
            visually_distinct_frame_threshold,
            max_candidates=max_candidates,
        )
    else:
        raw_candidates = [
            (fi, fr, compute_normalized_histogram(preprocess_frame(fr)))
            for fi, fr in list(frames_stream)[:max_candidates]
        ]

    if not raw_candidates:
        return [], None

    ## Encodage CLIP en un seul forward pass batch → embedding de scène + vecteurs par frame
    scene_embedding: Optional[np.ndarray] = None
    result: list[SceneCandidate] = []

    if use_clip and clip_selector is not None:
        clip_vectors = clip_selector.encode_frames_batch([fr for _, fr, _ in raw_candidates])

        vector_matrix = np.stack(clip_vectors)
        mean_vector = vector_matrix.mean(axis=0)
        norm = float(np.linalg.norm(mean_vector))
        scene_embedding = (
            (mean_vector / norm).astype(np.float32) if norm > 1e-12 else mean_vector.astype(np.float32)
        )
        result = [
            (fi, fr, hists, cv)
            for (fi, fr, hists), cv in zip(raw_candidates, clip_vectors)
        ]
    else:
        result = [(fi, fr, hists, None) for fi, fr, hists in raw_candidates]

    return result, scene_embedding


# =============================================================================
# SÉLECTION GLOBALE VIDÉO
# =============================================================================

def select_video_keyframes(
    all_candidates: list[SceneCandidate],
    visually_distinct_frame_threshold: float = 0.10,
    max_keyframes: Optional[int] = None,
) -> list[tuple[int, np.ndarray, Optional[np.ndarray]]]:
    """
    Sélectionne les keyframes visuellement les plus diversifiées à l'échelle de la vidéo.

    Algorithme : Farthest Point Sampling (FPS) incrémental.
    À chaque itération, on choisit le candidat dont la distance minimale à toutes les frames
    déjà sélectionnées est maximale — garantissant la diversité globale plutôt que de surcharger
    les premières occurrences d'une scène répétée.

    Complexité : O(N × K) avec mise à jour incrémentale de min_dist[i].
    Arrêt si le meilleur candidat restant est à moins de `visually_distinct_frame_threshold`
    de l'ensemble sélectionné (aucune frame assez distincte).

    Returns:
        list of (frame_idx, frame, clip_vec) — keyframes en ordre temporel.
    """
    if not all_candidates:
        return []

    ## Tri temporel pour déterminisme sur les ex-æquo de distance
    sorted_cands = sorted(all_candidates, key=lambda x: x[0])
    n = len(sorted_cands)

    ## Initialisation avec la première frame (ancre temporelle)
    selected_indices = [0]
    seed_hists = sorted_cands[0][2]

    ## min_dist[i] = distance minimale du candidat i à l'ensemble sélectionné
    min_dist = [
        bhattacharyya_distance(sorted_cands[i][2], seed_hists) if i > 0 else 0.0
        for i in range(n)
    ]

    remaining = list(range(1, n))

    while remaining:
        if max_keyframes is not None and len(selected_indices) >= max_keyframes:
            break

        ## Candidat le plus éloigné de tout ce qui est déjà sélectionné
        best = max(remaining, key=lambda i: min_dist[i])

        if min_dist[best] < visually_distinct_frame_threshold:
            break

        selected_indices.append(best)
        remaining.remove(best)

        ## Mise à jour incrémentale : min_dist[i] ne peut que diminuer
        new_hists = sorted_cands[best][2]
        for i in remaining:
            d = bhattacharyya_distance(sorted_cands[i][2], new_hists)
            if d < min_dist[i]:
                min_dist[i] = d

    selected_indices.sort()
    return [
        (sorted_cands[i][0], sorted_cands[i][1], sorted_cands[i][3])
        for i in selected_indices
    ]
