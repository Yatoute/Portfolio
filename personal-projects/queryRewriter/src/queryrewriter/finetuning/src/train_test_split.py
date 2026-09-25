from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Any

from .utils import read_jsonl, write_json, write_json_obj


def validate_row(row: dict[str, Any]) -> bool:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        return False
    roles = [m.get("role") for m in messages[:3] if isinstance(m, dict)]
    return roles[:3] == ["system", "user", "assistant"]


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Remove unwanted fields (like metadata) and keep only what LLaMA-Factory needs.
    """
    return {
        "messages": row["messages"]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to cleaned jsonl dataset")
    parser.add_argument("--output-dir", required=True, help="Directory for train/test json")
    parser.add_argument("--test-size", type=float, default=0.1, help="Test ratio, default 0.1")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = read_jsonl(args.input)

    # Validation + nettoyage
    rows = [clean_row(r) for r in rows if validate_row(r)]

    rng = random.Random(args.seed)
    rng.shuffle(rows)

    n_total = len(rows)
    n_test = max(1, int(n_total * args.test_size))
    test_rows = rows[:n_test]
    train_rows = rows[n_test:]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "train.json", train_rows)
    write_json(output_dir / "test.json", test_rows)

    stats = {
        "total_examples": n_total,
        "train_examples": len(train_rows),
        "test_examples": len(test_rows),
        "test_ratio": args.test_size,
        "seed": args.seed,
    }
    write_json_obj(output_dir / "stats.json", stats)

    print(stats)


if __name__ == "__main__":
    main()