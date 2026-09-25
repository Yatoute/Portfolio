from __future__ import annotations

import json
import re
import random
import hashlib
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List


RAW_EXAMPLES_PATH = Path("/data/workspace/AI/Labs/queryRewriter/data/intermediate/raw_examples.jsonl")
FINAL_JSONL = Path("/data/workspace/AI/Labs/queryRewriter/data/datasets/rewrite_dataset.jsonl")
FINAL_SFT_JSONL = Path("/data/workspace/AI/Labs/queryRewriter/data/datasets/rewrite_dataset_sft.jsonl")

RANDOM_SEED = 42
random.seed(RANDOM_SEED)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(rows: List[Dict[str, Any]], path: Path) -> None:
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(rows: List[Dict[str, Any]], path: Path) -> None:
    if path.exists():
        path.unlink()
    append_jsonl(rows, path)


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def example_signature(ex: Dict[str, Any]) -> str:
    history = ex.get("history", [])
    history_text = " || ".join(
        f"{m.get('role', '')}:{normalize_text(m.get('content', ''))}"
        for m in history
    )

    payload = {
        "entry_id": ex.get("entry_id", ""),
        "history": history_text,
        "user_query": normalize_text(ex.get("user_query", "")),
        "rewritten_query": normalize_text(ex.get("rewritten_query", "")),
        "should_rewrite": bool(ex.get("should_rewrite", False)),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deduplicate_examples(examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []

    for ex in examples:
        sig = example_signature(ex)
        if sig not in seen:
            seen.add(sig)
            out.append(ex)

    return out


def enforce_no_change_ratio(
    examples: List[Dict[str, Any]],
    target_ratio: float = 0.25,
) -> List[Dict[str, Any]]:
    no_change = [x for x in examples if not x.get("should_rewrite", False)]
    rewrite = [x for x in examples if x.get("should_rewrite", False)]

    if not examples:
        return examples

    desired_no_change = int(len(examples) * target_ratio)

    if len(no_change) <= desired_no_change:
        return examples

    random.shuffle(no_change)
    final = rewrite + no_change[:desired_no_change]
    random.shuffle(final)
    return final


def cap_bucket_size(
    examples: List[Dict[str, Any]],
    max_per_bucket: int,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}

    for ex in examples:
        rewrite_type = ex.get("rewrite_type", [])
        key = rewrite_type[0] if rewrite_type else "unknown"
        buckets.setdefault(key, []).append(ex)

    final: List[Dict[str, Any]] = []
    for _, rows in buckets.items():
        random.shuffle(rows)
        final.extend(rows[:max_per_bucket])

    random.shuffle(final)
    return final


def infer_topic_from_title(title: str) -> str:
    title = title.strip()
    if ":" in title:
        left = title.split(":", 1)[0].strip()
        if 2 <= len(left.split()) <= 8:
            return left
    return title


def generate_template_examples_for_entry(
    entry_id: str,
    primary_category: str,
    categories: List[str],
    title: str,
    limit: int = 1,
) -> List[Dict[str, Any]]:
    topic = infer_topic_from_title(title)

    candidate_templates: List[Dict[str, Any]] = [
        {
            "history": [
                {"role": "user", "content": "Can you summarize the paper?"},
                {"role": "assistant", "content": f"The paper focuses on `{topic}`."},
            ],
            "user_query": "How does this method work?",
            "rewritten_query": f"How does `{topic}` work?",
            "should_rewrite": True,
            "rewrite_type": ["coreference_resolution"],
            "difficulty": "easy",
        },
        {
            "history": [
                {"role": "user", "content": "What is the paper about?"},
                {"role": "assistant", "content": f"It studies `{topic}`."},
            ],
            "user_query": "And what are the limitations?",
            "rewritten_query": f"What are the limitations of `{topic}`?",
            "should_rewrite": True,
            "rewrite_type": ["ellipsis_resolution"],
            "difficulty": "medium",
        },
        {
            "history": [
                {"role": "user", "content": "What is the main idea of the paper?"},
                {"role": "assistant", "content": f"The main idea is `{topic}`."},
            ],
            "user_query": "So basically what is the main contribution?",
            "rewritten_query": "What is the main contribution of the paper?",
            "should_rewrite": True,
            "rewrite_type": ["remove_conversational_noise"],
            "difficulty": "easy",
        },
    ]

    chosen = candidate_templates[:limit]

    out = []
    for i, tpl in enumerate(chosen):
        out.append(
            {
                "entry_id": entry_id,
                "primary_category": primary_category,
                "categories": categories,
                "history": tpl["history"],
                "user_query": tpl["user_query"],
                "rewritten_query": tpl["rewritten_query"],
                "should_rewrite": tpl["should_rewrite"],
                "rewrite_type": tpl["rewrite_type"],
                "difficulty": tpl["difficulty"],
                "source": "template",
                "metadata": {
                    "title": title,
                },
            }
        )

    return out


def build_sft_example(ex: Dict[str, Any]) -> Dict[str, Any]:
    history = ex.get("history", [])
    history_str = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}"
        for m in history
    )

    user_content = (
        "Rewrite the user question into a standalone retrieval query for a ChatPDF system.\n\n"
        f"History:\n{history_str if history_str else '(empty)'}\n\n"
        f"Current user query:\n{ex['user_query']}"
    )

    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You rewrite user follow-up questions into retrieval-friendly standalone queries "
                    "for a ChatPDF system. Resolve references using history, remove conversational noise "
                    "when unnecessary, preserve the user's information need, and help retrieve the evidence "
                    "needed to answer the question. Do not answer the question."
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
            {
                "role": "assistant",
                "content": ex["rewritten_query"],
            },
        ],
        "metadata": {
            "entry_id": ex.get("entry_id"),
            "primary_category": ex.get("primary_category"),
            "categories": ex.get("categories", []),
            "should_rewrite": ex.get("should_rewrite"),
            "rewrite_type": ex.get("rewrite_type", []),
            "difficulty": ex.get("difficulty"),
            "source": ex.get("source"),
        },
    }


def recover_dataset_from_raw_examples(
    raw_examples_path: Path,
    final_jsonl: Path,
    final_sft_jsonl: Path,
    templates_per_entry: int = 1,
    max_examples: int = 15000,
    target_no_change_ratio: float = 0.25,
) -> None:
    raw_examples = load_jsonl(raw_examples_path)
    if not raw_examples:
        raise RuntimeError(f"No raw examples found in {raw_examples_path}")

    print(f"[INFO] Raw examples loaded: {len(raw_examples)}")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ex in raw_examples:
        grouped[ex["entry_id"]].append(ex)

    print(f"[INFO] Unique papers found: {len(grouped)}")

    all_examples: List[Dict[str, Any]] = []
    all_examples.extend(raw_examples)

    for entry_id, examples in grouped.items():
        first = examples[0]
        title = first.get("metadata", {}).get("title", entry_id)
        primary_category = first.get("primary_category", "")
        categories = first.get("categories", [])

        template_examples = generate_template_examples_for_entry(
            entry_id=entry_id,
            primary_category=primary_category,
            categories=categories,
            title=title,
            limit=templates_per_entry,
        )
        all_examples.extend(template_examples)

    print(f"[INFO] After template generation: {len(all_examples)}")

    all_examples = deduplicate_examples(all_examples)
    print(f"[INFO] After dedup: {len(all_examples)}")

    all_examples = enforce_no_change_ratio(
        all_examples,
        target_ratio=target_no_change_ratio,
    )
    print(f"[INFO] After no-change ratio enforcement: {len(all_examples)}")

    all_examples = cap_bucket_size(
        all_examples,
        max_per_bucket=int(max_examples * 0.50),
    )
    print(f"[INFO] After bucket cap: {len(all_examples)}")

    sft_examples = [build_sft_example(ex) for ex in all_examples]

    write_jsonl(all_examples, final_jsonl)
    write_jsonl(sft_examples, final_sft_jsonl)

    print(f"[DONE] Raw dataset written to: {final_jsonl}")
    print(f"[DONE] SFT dataset written to: {final_sft_jsonl}")


if __name__ == "__main__":
    recover_dataset_from_raw_examples(
        raw_examples_path=RAW_EXAMPLES_PATH,
        final_jsonl=FINAL_JSONL,
        final_sft_jsonl=FINAL_SFT_JSONL,
        templates_per_entry=1, 
        max_examples=15000,
        target_no_change_ratio=0.25,
    )
