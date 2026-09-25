import cv2
import numpy as np

from watchagent.settings import LOWRES_SIZE


def to_lowres_grayscale(
    frame: np.ndarray,
    size: tuple[int, int] = LOWRES_SIZE,
) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)


def frame_change_score(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(frame_a, frame_b)))


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


def bhattacharyya_distance(
    hists1: tuple[np.ndarray, np.ndarray, np.ndarray],
    hists2: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> float:
    d_h = cv2.compareHist(hists1[0], hists2[0], cv2.HISTCMP_BHATTACHARYYA)
    d_s = cv2.compareHist(hists1[1], hists2[1], cv2.HISTCMP_BHATTACHARYYA)
    d_v = cv2.compareHist(hists1[2], hists2[2], cv2.HISTCMP_BHATTACHARYYA)
    return float((d_h + d_s + d_v) / 3.0)