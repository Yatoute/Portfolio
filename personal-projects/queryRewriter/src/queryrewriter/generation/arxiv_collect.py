import time
import random
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import arxiv
import pandas as pd

from queryrewriter.generation.schemas import (
    ArxivMetadata, save_jsonl, save_csv,
    ARXIV_JSONL_PATH, ARXIV_CSV_PATH
)

# ============================================================
# CONFIG
# ============================================================

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# qualité minimale pour les abstracts
MIN_ABSTRACT_WORDS = 80

# fetch plus large que le quota final pour pouvoir filtrer
OVERFETCH_FACTOR = 3

# sécurité pour ne pas trop tirer d'un coup
MAX_RESULTS_PER_QUERY = 2000

# découpage temporel
TIME_BUCKETS = {
    "recent": (datetime(2023, 1, 1), datetime(2100, 1, 1)),
    "mid": (datetime(2020, 1, 1), datetime(2023, 1, 1)),
    "old": (datetime(1900, 1, 1), datetime(2020, 1, 1)),
}

TIME_WEIGHTS = {
    "recent": 0.50,
    "mid": 0.30,
    "old": 0.20,
}



# ============================================================
# STRATIFICATION PLAN
# ============================================================


STRATA_PLAN = {
    "core": {
        "weight": 0.50,
        "categories": {
            "cs.LG": 0.18,
            "cs.CL": 0.14,
            "cs.CV": 0.14,
            "cs.AI": 0.12,
            "cs.IR": 0.07,
            "cs.SE": 0.06,
            "cs.RO": 0.06,
            "cs.MA": 0.05,
            "stat.ML": 0.07,
            "eess.AS": 0.03,
            "eess.IV": 0.04,
            "eess.SP": 0.04,
        },
    },
    "adjacent": {
        "weight": 0.30,
        "categories": {
            "cs.DB": 0.10,
            "cs.DC": 0.08,
            "cs.DS": 0.08,
            "cs.LO": 0.08,
            "cs.NI": 0.08,
            "cs.OS": 0.06,
            "cs.PL": 0.08,
            "cs.CR": 0.08,
            "cs.SI": 0.08,
            "cs.CY": 0.05,
            "math.OC": 0.07,
            "math.ST": 0.07,
            "econ.EM": 0.07,
        },
    },
    "diversity": {
        "weight": 0.20,
        "categories": {
            "quant-ph": 0.125,
            "physics.data-an": 0.10,
            "physics.comp-ph": 0.10,
            "q-bio.QM": 0.10,
            "q-bio.NC": 0.075,
            "q-fin.CP": 0.10,
            "q-fin.ST": 0.10,
            "math.PR": 0.10,
            "math.CO": 0.075,
            "eess.SY": 0.125,
        },
    },
}

# ============================================================
# HELPERS
# ============================================================

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(normalize_whitespace(text).split())


def abstract_is_good(text: str) -> bool:
    if not text:
        return False
    clean = normalize_whitespace(text)
    wc = word_count(clean)
    if wc < MIN_ABSTRACT_WORDS :
        return False

    return True


def assign_time_bucket(dt: datetime) -> Optional[str]:

    # convertir en naive datetime UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

    for bucket_name, (start, end) in TIME_BUCKETS.items():
        if start <= dt < end:
            return bucket_name

    return None


def quota_from_weight(total: int, weight: float) -> int:
    return max(1, int(round(total * weight)))


def safe_get_pdf_url(result: arxiv.Result) -> Optional[str]:
    try:
        return result.pdf_url
    except Exception:
        return None


def extract_entry_url(result: arxiv.Result) -> Optional[str]:
    try:
        return result.entry_id
    except Exception:
        return None


def result_to_metadata(
    result: arxiv.Result,
    stratum_family: str,
    target_category: str,
) -> Optional[ArxivMetadata]:
    abstract = normalize_whitespace(result.summary or "")
    if not abstract_is_good(abstract):
        return None

    published = result.published
    updated = result.updated
    bucket = assign_time_bucket(published)
    if bucket is None:
        return None

    authors = [a.name for a in result.authors] if result.authors else []

    return ArxivMetadata(
        entry_id=result.entry_id,
        title=normalize_whitespace(result.title or ""),
        abstract=abstract,
        published=published.isoformat(),
        updated=updated.isoformat() if updated else published.isoformat(),
        primary_category=result.primary_category,
        categories=list(result.categories),
        authors=authors,
        pdf_url=safe_get_pdf_url(result),
        entry_url=extract_entry_url(result),
        comment=result.comment,
        journal_ref=result.journal_ref,
        doi=result.doi,
        time_bucket=bucket,
        stratum_family=stratum_family,
        target_category=target_category,
        abstract_word_count=word_count(abstract),
    )


# ============================================================
# ARXIV FETCH
# ============================================================

def fetch_category_results(
    client: arxiv.Client,
    category: str,
    max_results: int,
) -> List[arxiv.Result]:
    search = arxiv.Search(
        query=f"cat:{category}",
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance, #arxiv.SortCriterion.SubmittedDate
        sort_order=arxiv.SortOrder.Descending,
    )
    return list(client.results(search))


def collect_candidates_for_category(
    client: arxiv.Client,
    stratum_family: str,
    category: str,
    desired_quota: int,
) -> List[ArxivMetadata]:
    fetch_size = min(MAX_RESULTS_PER_QUERY, max(desired_quota * OVERFETCH_FACTOR, desired_quota))
    raw_results = fetch_category_results(client, category, fetch_size)

    converted: List[ArxivMetadata] = []
    for result in raw_results:
        item = result_to_metadata(
            result=result,
            stratum_family=stratum_family,
            target_category=category,
        )
        if item is not None:
            converted.append(item)

    return converted


# ============================================================
# STRATIFIED SAMPLING
# ============================================================

def stratified_sample_by_time(
    items: List[ArxivMetadata],
    desired_total: int,
) -> List[ArxivMetadata]:
    if len(items) <= desired_total:
        return items

    by_bucket: Dict[str, List[ArxivMetadata]] = {"recent": [], "mid": [], "old": []}
    for item in items:
        if item.time_bucket in by_bucket:
            by_bucket[item.time_bucket].append(item)

    sampled: List[ArxivMetadata] = []
    leftovers: List[ArxivMetadata] = []

    for bucket_name, bucket_items in by_bucket.items():
        random.shuffle(bucket_items)
        bucket_quota = quota_from_weight(desired_total, TIME_WEIGHTS[bucket_name])
        take = min(len(bucket_items), bucket_quota)
        sampled.extend(bucket_items[:take])
        leftovers.extend(bucket_items[take:])

    if len(sampled) < desired_total:
        random.shuffle(leftovers)
        sampled.extend(leftovers[: desired_total - len(sampled)])

    random.shuffle(sampled)
    return sampled[:desired_total]


def deduplicate_by_entry_id(items: List[ArxivMetadata]) -> List[ArxivMetadata]:
    seen = set()
    deduped = []
    for item in items:
        if item.entry_id not in seen:
            seen.add(item.entry_id)
            deduped.append(item)
    return deduped


def build_collection_plan(total_target: int) -> Dict[str, Dict[str, int]]:
    """
    Retourne:
    {
      "core": {"cs.LG": 900, ...},
      "adjacent": {...},
      ...
    }
    """
    plan: Dict[str, Dict[str, int]] = {}
    for family_name, family_cfg in STRATA_PLAN.items():
        family_total = quota_from_weight(total_target, family_cfg["weight"])
        family_plan: Dict[str, int] = {}
        for category, cat_weight in family_cfg["categories"].items():
            family_plan[category] = quota_from_weight(family_total, cat_weight)
        plan[family_name] = family_plan
    return plan

def print_summary(items: List[ArxivMetadata]) -> None:
    if not items:
        print("No items collected.")
        return

    df = pd.DataFrame([asdict(x) for x in items])

    print("\n=== Global summary ===")
    print(f"Collected: {len(df)}")

    print("\n=== By target_category ===")
    print(df["target_category"].value_counts().sort_index())

    print("\n=== By time_bucket ===")
    print(df["time_bucket"].value_counts().sort_index())

    print("\n=== By stratum_family ===")
    print(df["stratum_family"].value_counts().sort_index())

    print("\n=== Abstract word count ===")
    print(df["abstract_word_count"].describe())


# ============================================================
# MAIN
# ============================================================

def collect_arxiv_metadata(total_target: int = 1000) -> List[ArxivMetadata]:
    client = arxiv.Client()

    plan = build_collection_plan(total_target)
    all_items: List[ArxivMetadata] = []
    for family_name, family_plan in plan.items():
        print(f"\n[Family] {family_name}")
        for category, quota in family_plan.items():
            print(f"  - Collecting category={category} target={quota}")
            try:
                candidates = collect_candidates_for_category(
                    client=client,
                    stratum_family=family_name,
                    category=category,
                    desired_quota=quota,
                )
                sampled = stratified_sample_by_time(candidates, quota)
                print(
                    f"    fetched_good={len(candidates)} sampled={len(sampled)}"
                )
                all_items.extend(sampled)
            except Exception as e:
                print(f"Failed to collect category={category} target={quota} : {e}")
                all_items = deduplicate_by_entry_id(all_items)
                save_jsonl(all_items, ARXIV_JSONL_PATH)
                save_csv(all_items, ARXIV_CSV_PATH)
                
                time.sleep(5*60)
                
                candidates = collect_candidates_for_category(
                    client=client,
                    stratum_family=family_name,
                    category=category,
                    desired_quota=quota,
                )
                sampled = stratified_sample_by_time(candidates, quota)
                print(
                    f"    fetched_good={len(candidates)} sampled={len(sampled)}"
                )
                all_items.extend(sampled)
                
                
            
            time.sleep(10)
        
        time.sleep(3*60)

    all_items = deduplicate_by_entry_id(all_items)

    return all_items


if __name__ == "__main__":
    items = collect_arxiv_metadata(total_target=12000)

    save_jsonl(items, ARXIV_JSONL_PATH)
    save_csv(items, ARXIV_CSV_PATH)
    print_summary(items)

    print(f"\nSaved JSONL to: {ARXIV_JSONL_PATH}")
    print(f"Saved CSV to:   {ARXIV_CSV_PATH}")