from __future__ import annotations

import argparse
import asyncio
import json
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from tqdm import tqdm

load_dotenv()
MODEL = "gpt-5-nano"
CONCURRENCY = 10
MAX_RETRIES = 3

SYSTEM_PROMPT = """
You convert a rewritten retrieval query into a JSON object for multi-query retrieval.

Output format:
{"queries": ["query1", "query2"]}

Rules:
- Output valid JSON only
- Do not answer the question
- Preserve the original intent
- If one query is sufficient return a single query
- If the query contains multiple distinct retrieval intents, decompose it into multiple focused queries
""".strip()

USER_TEMPLATE = """
Convert the following rewritten retrieval query into JSON.

Rewritten query:
{query}
""".strip()

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        }
    },
    "required": ["queries"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=CONCURRENCY,
        help=f"Number of parallel requests (default: {CONCURRENCY})",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"Max retries per request (default: {MAX_RETRIES})",
    )
    parser.add_argument(
        "--skip-already-json",
        action="store_true",
        help="Skip rows whose assistant content is already {'queries': [...]}",
    )
    return parser.parse_args()


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_no}: {exc}") from exc
    return rows


def extract_assistant_message(row: Dict[str, Any]) -> Dict[str, Any]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Row has no valid 'messages' list")

    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return msg

    raise ValueError("Assistant message not found")


def validate_result(obj: Any) -> Dict[str, List[str]]:
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")

    queries = obj.get("queries")
    if not isinstance(queries, list) or len(queries) == 0:
        raise ValueError("Field 'queries' must be a non-empty list")

    cleaned: List[str] = []
    for q in queries:
        if not isinstance(q, str):
            raise ValueError("Each item in 'queries' must be a string")
        q = q.strip()
        if q:
            cleaned.append(q)

    if not cleaned:
        raise ValueError("All queries are empty after cleanup")

    return {"queries": cleaned}


def parse_existing_queries_json(text: str) -> Optional[Dict[str, List[str]]]:
    try:
        obj = json.loads(text)
        return validate_result(obj)
    except Exception:
        return None


async def convert_query(
    client: AsyncOpenAI,
    query: str,
    max_retries: int,
) -> Dict[str, List[str]]:
    user_prompt = USER_TEMPLATE.format(query=query.strip())

    for attempt in range(max_retries):
        try:
            response = await client.responses.create(
                model=MODEL,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "queries_output",
                        "strict": True,
                        "schema": SCHEMA,
                    }
                },
            )

            raw = response.output_text
            data = json.loads(raw)
            return validate_result(data)

        except Exception:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(1.5 * (attempt + 1))

    raise RuntimeError("Unexpected retry loop exit")


async def worker(
    semaphore: asyncio.Semaphore,
    write_lock: asyncio.Lock,
    client: AsyncOpenAI,
    row: Dict[str, Any],
    writer,
    max_retries: int,
    skip_already_json: bool,
) -> None:
    async with semaphore:
        assistant_msg = extract_assistant_message(row)
        rewritten_query = assistant_msg.get("content")

        if not isinstance(rewritten_query, str):
            raise ValueError("Assistant content is not a string")

        result: Dict[str, List[str]]
        if skip_already_json:
            parsed = parse_existing_queries_json(rewritten_query)
            if parsed is not None:
                result = parsed
            else:
                result = await convert_query(client, rewritten_query, max_retries)
        else:
            result = await convert_query(client, rewritten_query, max_retries)

        assistant_msg["content"] = json.dumps(result, ensure_ascii=False)

        if "metadata" not in row or not isinstance(row["metadata"], dict):
            row["metadata"] = {}

        row["metadata"]["output_format"] = "json_queries"
        row["metadata"]["query_list_conversion_model"] = MODEL
        row["metadata"]["query_list_schema"] = '{"queries": ["query1", "query2"]}'

        async with write_lock:
            writer.write(json.dumps(row, ensure_ascii=False) + "\n")
            writer.flush()


async def main(
    input_path: str,
    output_path: str,
    concurrency: int,
    max_retries: int,
    skip_already_json: bool,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = AsyncOpenAI(api_key=api_key)
    rows = read_jsonl(input_path)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()

    with output_file.open("w", encoding="utf-8") as writer:
        tasks = [
            worker(
                semaphore=semaphore,
                write_lock=write_lock,
                client=client,
                row=row,
                writer=writer,
                max_retries=max_retries,
                skip_already_json=skip_already_json,
            )
            for row in rows
        ]

        for fut in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            await fut


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        main(
            input_path=args.input,
            output_path=args.output,
            concurrency=args.concurrency,
            max_retries=args.max_retries,
            skip_already_json=args.skip_already_json,
        )
    )
    
