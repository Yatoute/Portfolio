import os
import random
from dataclasses import asdict
from typing import Any, Dict, List

from queryrewriter.generation.dedup import deduplicate_examples
from queryrewriter.generation.extractor import extract_examples_from_conversation
from queryrewriter.generation.filters import filter_example
from queryrewriter.generation.generator import generate_llm_conversation
from queryrewriter.generation.schemas import (
    RewriteExample,
    RAW_CONVERSATIONS_PATH,
    RAW_EXAMPLES_PATH,
    VALIDATED_EXAMPLES_PATH,
    FINAL_JSONL,
    FINAL_SFT_JSONL,
    USE_LLM_GENERATION,
    USE_TEMPLATE_GENERATION,
    append_jsonl,
    load_arxiv_metadata_jsonl,
)
from queryrewriter.generation.templates import generate_template_examples
from queryrewriter.generation.validator import validate_example


def build_sft_example(ex: RewriteExample) -> Dict[str, Any]:
    history_str = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in ex.history])
    user_content = (
        "Rewrite the user question into a standalone retrieval query for a ChatPDF system.\n\n"
        f"History:\n{history_str if history_str else '(empty)'}\n\n"
        f"Current user query:\n{ex.user_query}"
    )

    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You rewrite user follow-up questions into retrieval-friendly standalone queries "
                    "for a ChatPDF system. Resolve references using history, remove conversational noise "
                    "when unnecessary, preserve the user's intent, and keep the query unchanged if it is "
                    "already optimal for retrieval. Do not answer the question."
                ),
            },
            {
                "role": "user",
                "content": user_content,
            },
            {
                "role": "assistant",
                "content": ex.rewritten_query,
            },
        ],
        "metadata": {
            "entry_id": ex.entry_id,
            "primary_category": ex.primary_category,
            "categories": ex.categories,
            "should_rewrite": ex.should_rewrite,
            "rewrite_type": ex.rewrite_type,
            "difficulty": ex.difficulty,
            "source": ex.source,
        },
    }


def enforce_no_change_ratio(examples: List[RewriteExample], target_ratio: float = 0.20) -> List[RewriteExample]:
    no_change = [x for x in examples if not x.should_rewrite]
    rewrite = [x for x in examples if x.should_rewrite]

    if not examples:
        return examples

    desired_no_change = int(len(examples) * target_ratio)
    if len(no_change) <= desired_no_change:
        return examples

    random.shuffle(no_change)
    final = rewrite + no_change[:desired_no_change]
    random.shuffle(final)
    return final


def cap_bucket_size(examples: List[RewriteExample], max_per_bucket: int = 5000) -> List[RewriteExample]:
    buckets: Dict[str, List[RewriteExample]] = {}
    for ex in examples:
        key = ex.rewrite_type[0] if ex.rewrite_type else "unknown"
        buckets.setdefault(key, []).append(ex)

    final = []
    for _, rows in buckets.items():
        random.shuffle(rows)
        final.extend(rows[:max_per_bucket])

    random.shuffle(final)
    return final


def run_pipeline(
    metadata_jsonl_path: str,
    conversations_per_paper: int = 1,
    templates_per_paper: int = 1,
    validate_with_llm: bool = True,
    max_examples: int = 1000,
    start_idx: int = 0,
) -> None:
    papers = load_arxiv_metadata_jsonl(metadata_jsonl_path)
    papers = papers[start_idx:]
    
    END_IDX = 4000
    papers = papers[:END_IDX]
        
    if not papers:
        raise RuntimeError("No ArxivMetadata found.")

    all_examples: List[RewriteExample] = []

    for paper in papers:
        
        print(f"[PAPER] {paper.entry_id} | {paper.title[:80]}")

        if USE_LLM_GENERATION:
            for i in range(conversations_per_paper):
                try:
                    conversation = generate_llm_conversation(paper)
                    append_jsonl(
                        [{"paper":paper.entry_id, "conv_id": i, "conversation": conversation}], 
                        RAW_CONVERSATIONS_PATH
                    )
                    
                    extracted = extract_examples_from_conversation(paper, conversation, i)
                    append_jsonl(
                        [asdict(ex) for ex in extracted], 
                        RAW_EXAMPLES_PATH
                    )
                    
                    for ex in extracted:
                        if filter_example(ex):
                            all_examples.append(ex)

                except Exception as e:
                    print(f"[WARN] LLM pipeline failed on {paper.entry_id}/{i}: {e}")

        if USE_TEMPLATE_GENERATION and len(all_examples) < max_examples:
            try:
                template_examples = generate_template_examples(paper, per_paper_limit=templates_per_paper)
                for ex in template_examples:
                    if filter_example(ex):
                        all_examples.append(ex)
            except Exception as e:
                print(f"[WARN] Template generation failed on {paper.entry_id}: {e}")

        if len(all_examples) >= max_examples:
            break

    print(f"[INFO] Before validation: {len(all_examples)}")

    if validate_with_llm:
        validated = []
        for idx, ex in enumerate(all_examples):
            try:
                corrected = validate_example(ex)

                if corrected is not None:
                    append_jsonl([asdict(corrected)], VALIDATED_EXAMPLES_PATH)

                    if filter_example(corrected):
                        validated.append(corrected)

            except Exception as e:
                print(f"[WARN] Validation failed at idx={idx}: {e}")

        all_examples = validated
        print(f"[INFO] After validation: {len(all_examples)}")

    all_examples = deduplicate_examples(all_examples)
    print(f"[INFO] After dedup: {len(all_examples)}")

    all_examples = enforce_no_change_ratio(all_examples, target_ratio=0.25)
    all_examples = cap_bucket_size(all_examples, max_per_bucket=int(max_examples * 0.50))
    print(f"[INFO] After balancing: {len(all_examples)}")

    append_jsonl([asdict(x) for x in all_examples], FINAL_JSONL)

    append_jsonl([build_sft_example(x) for x in all_examples], FINAL_SFT_JSONL)

    print(f"[DONE] Raw dataset: {FINAL_JSONL}")
    print(f"[DONE] SFT dataset: {FINAL_SFT_JSONL}")