from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from queryrewriter.generation.build_dataset import run_pipeline
from queryrewriter.generation.schemas import ARXIV_OUTPUT_DIR


def run_one_chunk(chunk_path: Path, max_examples: int = 10, start_idx: int = 0, end_idx: int = -1) -> str:
    run_pipeline(
        metadata_jsonl_path=chunk_path,
        max_examples=max_examples,
        validate_with_llm=False,
        start_idx=start_idx
    )
    return f"Done: {chunk_path}"


if __name__ == "__main__":
    chunk_paths = [
        f"{ARXIV_OUTPUT_DIR}/chunk1.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk2.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk3.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk4.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk5.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk6.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk7.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk8.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk9.jsonl",
        f"{ARXIV_OUTPUT_DIR}/chunk10.jsonl",
    ]

    # 5 workers max ici, à ajuster
    MAX_WORKERS = 10
    START_IDX = 0
    MAX_EXAMPLES_PER_CHUNK = 14000
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(run_one_chunk, path, MAX_EXAMPLES_PER_CHUNK, START_IDX) for path in chunk_paths]

        for future in as_completed(futures):
            try:
                print(future.result())
            except Exception as e:
                print(f"[ERROR] {e}")