import os
import re
import json
import random
import hashlib
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import List, Literal, Dict, Any, Optional

from pydantic import BaseModel, Field, ConfigDict

import pandas as pd

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

ARXIV_OUTPUT_DIR = os.path.join(BASE_DIR, "data/arxiv_metadata")

ARXIV_JSONL_PATH = os.path.join(ARXIV_OUTPUT_DIR, "arxiv_stratified_metadata.jsonl")
ARXIV_CSV_PATH = os.path.join(ARXIV_OUTPUT_DIR, "arxiv_stratified_metadata.csv")

DATASETS_DIR = os.path.join(BASE_DIR, "data/datasets")
INTERMEDIATE_DIR = os.path.join(BASE_DIR, "data/intermediate")

RAW_CONVERSATIONS_PATH = os.path.join(INTERMEDIATE_DIR, "raw_conversations.jsonl")
RAW_EXAMPLES_PATH= os.path.join(INTERMEDIATE_DIR, "raw_examples.jsonl")
VALIDATED_EXAMPLES_PATH = os.path.join(INTERMEDIATE_DIR, "validated_examples.jsonl")

FINAL_JSONL = os.path.join(DATASETS_DIR, "rewrite_dataset.jsonl")
FINAL_SFT_JSONL = os.path.join(DATASETS_DIR, "rewrite_dataset_sft.jsonl")

for d in [ARXIV_OUTPUT_DIR, DATASETS_DIR, INTERMEDIATE_DIR]:
    os.makedirs(d, exist_ok=True)

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

USE_LLM_GENERATION = True
USE_TEMPLATE_GENERATION = True

TARGET_NO_CHANGE_RATIO = 0.20

MIN_QUERY_WORDS = 3
MAX_QUERY_WORDS = 28

DEFAULT_TEMPERATURE_CONV = 0.7
DEFAULT_TEMPERATURE_EXTRACT = 0.3
DEFAULT_TEMPERATURE_VALIDATE = 0.0




@dataclass
class ArxivMetadata:
    entry_id: str
    title: str
    abstract: str
    published: str
    updated: str
    primary_category: str
    categories: List[str]
    authors: List[str]
    pdf_url: Optional[str]
    entry_url: Optional[str]
    comment: Optional[str]
    journal_ref: Optional[str]
    doi: Optional[str]
    time_bucket: str
    stratum_family: str
    target_category: str
    abstract_word_count: int


@dataclass
class RewriteExample:
    entry_id: str
    primary_category: str
    categories: List[str]
    history: List[Dict[str, str]]
    user_query: str
    rewritten_query: str
    should_rewrite: bool
    rewrite_type: List[str]
    difficulty: str
    source: str
    metadata: Dict[str, Any]



class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
class ConversationMessage(StrictBaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

class ConversationNotes(StrictBaseModel):
    style: str
    user_profile: str


class ConversationGenerationResponse(StrictBaseModel):
    conversation: List[ConversationMessage]
    notes: ConversationNotes

class ExtractedExample(StrictBaseModel):
    history: List[ConversationMessage]
    user_query: str = Field(min_length=1)
    rewritten_query: str = Field(min_length=1)
    should_rewrite: bool
    rewrite_type: List[
        Literal[
            "coreference_resolution",
            "remove_conversational_noise",
            "clarification",
            "query_expansion",
        ]
    ]
    difficulty: Literal["easy", "medium", "hard"]

class ExtractedExamplesResponse(StrictBaseModel):
    examples: List[ExtractedExample]

class ValidationResponse(StrictBaseModel):
    valid: bool
    reason: str
    corrected_rewritten_query: str
    corrected_should_rewrite: bool
    corrected_rewrite_type: List[
        Literal[
            "coreference_resolution",
            "remove_conversational_noise",
            "clarification",
            "query_expansion",
        ]
    ]
    corrected_difficulty: Literal["easy", "medium", "hard"]
    
# ============================================================
# IO UTILS
# ============================================================

def save_jsonl(items: List[ArxivMetadata | RewriteExample], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

def save_csv(items: List[ArxivMetadata | RewriteExample], path: Path) -> None:
    df = pd.DataFrame([asdict(x) for x in items])
    df.to_csv(path, index=False)

def load_arxiv_metadata_jsonl(path: str) -> List[ArxivMetadata]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            items.append(ArxivMetadata(**json.loads(line)))
    return items

def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        
def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(rows: List[Dict[str, Any]], path: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def write_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ============================================================
# TEXTS UTILS
# ============================================================
def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_json_parse(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```json", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        pass

    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [x for x in [start_obj, start_arr] if x != -1]
    if not starts:
        raise ValueError("No JSON found.")

    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    if end == -1 or end <= start:
        raise ValueError("Malformed JSON.")

    candidate = text[start:end + 1]
    return json.loads(candidate)

