from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable
import random


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {i} in {path}: {e}") from e
    return rows


def write_json(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(list(rows), f, ensure_ascii=False, indent=2)


def write_json_obj(path: str | Path, obj: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def validate_row(row: dict[str, Any]) -> bool:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        return False
    roles = [m.get("role") for m in messages[:3] if isinstance(m, dict)]
    return roles[:3] == ["system", "user", "assistant"]


def train_test_split(input_path: str, output_dir: str, test_size: float = 0.1, seed: int = 42) -> None:
   
    rows = read_jsonl(input_path)
    rows = [r for r in rows if validate_row(r)]

    rng = random.Random(seed)
    rng.shuffle(rows)

    n_total = len(rows)
    n_test = max(1, int(n_total * test_size))
    test_rows = rows[:n_test]
    train_rows = rows[n_test:]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "train.json", train_rows)
    write_json(output_dir / "test.json", test_rows)

    stats = {
        "total_examples": n_total,
        "train_examples": len(train_rows),
        "test_examples": len(test_rows),
        "test_ratio": test_size,
        "seed": seed,
    }
    write_json_obj(output_dir / "stats.json", stats)

    return stats
