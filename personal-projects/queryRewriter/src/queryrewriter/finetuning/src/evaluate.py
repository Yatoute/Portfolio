from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from .utils import read_jsonl
from .metrics import SemanticSimilarity, exact_match, token_f1


def extract_assistant_text(row: dict[str, Any]) -> str:
    messages = row["messages"]
    for msg in messages:
        if msg["role"] == "assistant":
            return msg["content"]
    raise ValueError("No assistant message found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--references", required=True, help="Original cleaned jsonl or json test file")
    parser.add_argument("--predictions", required=True, help="JSONL with {'prediction': ...}")
    args = parser.parse_args()

    if args.references.endswith(".jsonl"):
        refs_rows = read_jsonl(args.references)
    else:
        with open(args.references, "r", encoding="utf-8") as f:
            refs_rows = json.load(f)

    pred_rows = read_jsonl(args.predictions)

    refs = [extract_assistant_text(r) for r in refs_rows]
    preds = [r["prediction"] for r in pred_rows]

    if len(refs) != len(preds):
        raise ValueError(f"Mismatch refs={len(refs)} preds={len(preds)}")

    em = mean(exact_match(p, r) for p, r in zip(preds, refs))
    f1 = mean(token_f1(p, r) for p, r in zip(preds, refs))

    sem_model = SemanticSimilarity()
    sem = mean(sem_model.score_many(preds, refs))

    print(
        json.dumps(
            {
                "num_examples": len(refs),
                "exact_match": round(em, 4),
                "token_f1": round(f1, 4),
                "semantic_similarity": round(sem, 4),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
