from __future__ import annotations

import json
import re

import torch
from peft import AutoPeftModelForCausalLM
from transformers import AutoTokenizer
from transformers import AutoModelForCausalLM

BASE_MODEL = "Qwen/Qwen3.5-9B"
ADAPTER_PATH = "/workspace/queryrewriter-train/saves/qwen3_5_9b_rewriter_qlora/checkpoint-866"


def build_messages() -> list[dict[str, str]]:
    system = (
        "You are a conversational query rewriting model for semantic retrieval.\n\n"
        "Your task is to convert the user's current question into one or more standalone "
        "retrieval queries that can be used by a RAG system.\n\n"
        'The output MUST be valid JSON following this schema:\n'
        '{"queries": ["query1", "query2"]}\n\n'
        # "Rules:\n"
        # "- Do not answer the question.\n"
        # "- Do not explain anything.\n"
        # "- Do not include any thinking process.\n"
        # "- Do not include markdown.\n"
        # "- Output only the JSON object.\n"
        # "- Each query must be a standalone search query.\n"
        # "- Preserve the user's original intent.\n"
        # "- Resolve references using the conversation history when necessary.\n"
        # "- Remove conversational filler and unnecessary wording.\n"
        # "- Do not introduce facts not present in the conversation.\n"
        # "- If the question expresses multiple information needs, split them into multiple focused queries.\n"
        # "- If a single query is sufficient, return exactly one item in the list.\n"
        # "- Usually return between 1 and 4 queries.\n"
    )

    user = (
        "Rewrite the user's current question into retrieval queries for a RAG system.\n\n"
        "History:\n"
        "User: What is the main contribution of the paper?\n"
        "Assistant: The paper proposes a graph-based retrieval framework.\n"
        "User: How does it compare to BM25?\n"
        "Assistant: It improves recall with graph expansion.\n\n"
        "Current user query:\n"
        "What about its scalability on larger corpora?"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_json_object(text: str) -> dict | None:
    text = text.strip()

    # Essai direct
    try:
        obj = json.loads(text)
        if is_valid_prediction(obj):
            return obj
    except Exception:
        pass

    # Fallback : extraire le premier objet JSON plausible
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    candidate = match.group(0)
    try:
        obj = json.loads(candidate)
        if is_valid_prediction(obj):
            return obj
    except Exception:
        return None

    return None


def is_valid_prediction(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    queries = obj.get("queries")
    if not isinstance(queries, list) or len(queries) == 0:
        return False
    if not all(isinstance(q, str) and q.strip() for q in queries):
        return False
    return True


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(
        BASE_MODEL,
        trust_remote_code=True,
    )

    # model = AutoPeftModelForCausalLM.from_pretrained(
    #     ADAPTER_PATH,
    #     torch_dtype=torch.bfloat16,
    #     device_map="auto",
    #     trust_remote_code=True,
    # )
    # model.eval()


    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()

    messages = build_messages()

    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=256,
            do_sample=False,
            temperature=None,
            top_p=None,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    generated_ids = outputs[0][inputs["input_ids"].shape[1]:]
    raw_output = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    print("=== RAW OUTPUT ===")
    print(raw_output)

    parsed = extract_json_object(raw_output)

    print("\n=== JSON CHECK ===")
    if parsed is None:
        print("Invalid JSON output")
    else:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()