from __future__ import annotations

import re
from typing import Iterable

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[?.!]+$", "", text)
    return text


def exact_match(pred: str, ref: str) -> float:
    return 1.0 if normalize(pred) == normalize(ref) else 0.0


def token_f1(pred: str, ref: str) -> float:
    pred_tokens = normalize(pred).split()
    ref_tokens = normalize(ref).split()

    if not pred_tokens and not ref_tokens:
        return 1.0
    if not pred_tokens or not ref_tokens:
        return 0.0

    pred_set = pred_tokens
    ref_set = ref_tokens
    common = 0
    ref_count = {}
    for t in ref_set:
        ref_count[t] = ref_count.get(t, 0) + 1
    for t in pred_set:
        if ref_count.get(t, 0) > 0:
            common += 1
            ref_count[t] -= 1

    if common == 0:
        return 0.0

    precision = common / len(pred_tokens)
    recall = common / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)


class SemanticSimilarity:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)

    def score_many(self, preds: Iterable[str], refs: Iterable[str]) -> list[float]:
        preds = list(preds)
        refs = list(refs)
        pred_emb = self.model.encode(preds, normalize_embeddings=True)
        ref_emb = self.model.encode(refs, normalize_embeddings=True)
        sims = cosine_similarity(pred_emb, ref_emb)
        return [float(sims[i, i]) for i in range(len(preds))]
