import json
from typing import List

from queryrewriter.generation.schemas import RewriteExample, normalize_text, stable_hash


def example_signature(ex: RewriteExample) -> str:
    history_text = " || ".join(f"{m['role']}:{normalize_text(m['content'])}" for m in ex.history)
    payload = json.dumps(
        {
            "doc_id": ex.entry_id,
            "history": history_text,
            "user_query": normalize_text(ex.user_query),
            "rewritten_query": normalize_text(ex.rewritten_query),
            "should_rewrite": ex.should_rewrite,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return stable_hash(payload)


def deduplicate_examples(examples: List[RewriteExample]) -> List[RewriteExample]:
    seen = set()
    out = []
    for ex in examples:
        sig = example_signature(ex)
        if sig not in seen:
            seen.add(sig)
            out.append(ex)
    return out