import gc
import logging
import os
import shutil
import time
from typing import Optional

import psutil
import torch
import whisperx


# =============================================================================
# CONFIGURATION MEMOIRE
# =============================================================================

DEFAULT_ALLOCATED_MEMORY = "18Gi"
DEFAULT_RESERVED_AVAILABLE_MEMORY = "4Gi"
DEFAULT_CLEANING_MARGIN = "2Gi"

RAW_ALLOCATED_MEMORY = (
    os.environ.get("ALLOCATED_MEMORY")
    or DEFAULT_ALLOCATED_MEMORY
)

RAW_RESERVED_AVAILABLE_MEMORY = (
    os.environ.get("MIN_AVAILABLE_MEMORY")
    or DEFAULT_RESERVED_AVAILABLE_MEMORY
)

RAW_CLEANING_MARGIN = (
    os.environ.get("CLEANING_MARGIN")
    or DEFAULT_CLEANING_MARGIN
)

# Répertoire racine du cache disque des modèles d'alignement
ALIGN_MODELS_CACHE_ROOT_DIR = os.environ.get(
    "ALIGN_MODELS_CACHE_ROOT_DIR",
    "/tmp/whisperx_align_models",
)


def parse_size_to_gib(value: Optional[str | int | float]) -> float:
    if value is None:
        raise ValueError("Memory size value cannot be None")

    if isinstance(value, (int, float)):
        return float(value)

    raw_value = str(value).strip().lower()

    units = {
        "gib": 1.0,
        "gi": 1.0,
        "gb": 1.0,
        "g": 1.0,
        "mib": 1.0 / 1024.0,
        "mi": 1.0 / 1024.0,
        "mb": 1.0 / 1024.0,
        "m": 1.0 / 1024.0,
        "kib": 1.0 / (1024.0 ** 2),
        "ki": 1.0 / (1024.0 ** 2),
        "kb": 1.0 / (1024.0 ** 2),
        "k": 1.0 / (1024.0 ** 2),
        "b": 1.0 / (1024.0 ** 3),
    }

    for suffix, factor in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if raw_value.endswith(suffix):
            numeric_part = raw_value[:-len(suffix)].strip()
            return float(numeric_part) * factor

    return float(raw_value)


ALLOCATED_MEMORY_GIB = parse_size_to_gib(RAW_ALLOCATED_MEMORY)
RESERVED_AVAILABLE_MEMORY_GIB = parse_size_to_gib(RAW_RESERVED_AVAILABLE_MEMORY)
CLEANING_MARGIN_GIB = parse_size_to_gib(RAW_CLEANING_MARGIN)


# =============================================================================
# CACHE GLOBAL DES MODELES D'ALIGNEMENT
# =============================================================================

ALIGN_MODELS_CACHE: dict[str, dict] = {}
ALIGN_MODEL_LAST_USED_AT: dict[str, float] = {}


# =============================================================================
# HELPERS
# =============================================================================

def normalize_language_code(language_code: str) -> str:
    return language_code.strip().lower()


def ensure_directory_exists(path: str):
    os.makedirs(path, exist_ok=True)


def safe_remove_path(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        logging.warning(f"⚠️ Impossible de supprimer {path}: {exc}")


def get_align_model_cache_dir(language_code: str) -> str:
    
    return os.path.join(ALIGN_MODELS_CACHE_ROOT_DIR, language_code)


def log_memory_state(prefix: str):
    logging.info(
        f"{prefix} | "
        f"rss_gib={get_process_rss_gib():.2f} | "
        f"tmp_used_gib={get_tmp_used_gib():.2f} | "
        f"pressure_gib={get_estimated_memory_pressure_gib():.2f} | "
        f"threshold_gib={get_cleaning_threshold_gib():.2f} | "
        f"target_gib={get_target_pressure_after_cleaning_gib():.2f}"
    )


# =============================================================================
# METRIQUES
# =============================================================================

def get_tmp_used_gib() -> float:
    _, used_bytes, _ = shutil.disk_usage("/tmp")
    return used_bytes / (1024 ** 3)


def get_process_rss_gib() -> float:
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 ** 3)


def get_estimated_memory_pressure_gib() -> float:
    return get_process_rss_gib() + get_tmp_used_gib()


def get_cleaning_threshold_gib() -> float:
    return max(0.0, ALLOCATED_MEMORY_GIB - RESERVED_AVAILABLE_MEMORY_GIB)


def get_target_pressure_after_cleaning_gib() -> float:
    return max(
        0.0,
        ALLOCATED_MEMORY_GIB - RESERVED_AVAILABLE_MEMORY_GIB - CLEANING_MARGIN_GIB,
    )


def is_cleaning_required(current_pressure_gib: float) -> bool:
    return current_pressure_gib >= get_cleaning_threshold_gib()


def is_pressure_back_to_safe_target(current_pressure_gib: float) -> bool:
    return current_pressure_gib <= get_target_pressure_after_cleaning_gib()


# =============================================================================
# OUTILS DE NETTOYAGE
# =============================================================================

def cleanup_standard_disk_caches():
    """
    Dernier recours uniquement.
    Cette fonction est globale et plus destructive que l'éviction ciblée
    par langue.
    """
    paths = []

    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        paths.append(hf_home)

    paths.append(os.path.expanduser("~/.cache/torch"))
    paths.append(os.path.expanduser("~/.cache/huggingface"))

    cleaned_paths = []

    for path in paths:
        if os.path.exists(path):
            logging.warning(f"🧹 Global disk cache cleanup | path={path}")
            safe_remove_path(path)
            os.makedirs(path, exist_ok=True)
            cleaned_paths.append(path)

    if cleaned_paths:
        log_memory_state("🧹 After global disk cache cleanup")
    else:
        logging.info("🧹 No standard disk cache to clean")


def evict_align_model_from_memory_and_disk(language_code: str, device: str = "cpu"):

    cached = ALIGN_MODELS_CACHE.pop(language_code, None)
    ALIGN_MODEL_LAST_USED_AT.pop(language_code, None)

    model_cache_dir = None

    if cached:
        try:
            model_cache_dir = cached.pop("model_cache_dir", None)

            align_model = cached.pop("align_model", None)
            align_metadata = cached.pop("align_metadata", None)

            del align_model
            del align_metadata
            cached.clear()

        except Exception as exc:
            logging.warning(
                f"⚠️ Error while evicting align model refs | "
                f"lang={language_code} | error={exc}"
            )

    gc.collect()

    if device == "cuda":
        torch.cuda.empty_cache()

    if model_cache_dir and os.path.exists(model_cache_dir):
        logging.info(
            f"🧹 Removing align model disk cache | "
            f"lang={language_code} | path={model_cache_dir}"
        )
        safe_remove_path(model_cache_dir)

    # On recrée le dossier vide pour garder une structure stable
    if model_cache_dir:
        ensure_directory_exists(model_cache_dir)


def sort_languages_by_lru(languages: list[str]) -> list[str]:
    return sorted(
        languages,
        key=lambda lang: ALIGN_MODEL_LAST_USED_AT.get(lang, 0.0),
    )


def evict_non_priority_align_models(priority_languages: list[str], device: str) -> list[str]:
    priority_set = {normalize_language_code(lang) for lang in priority_languages}

    candidates = [
        language_code
        for language_code in ALIGN_MODELS_CACHE.keys()
        if language_code not in priority_set
    ]
    candidates = sort_languages_by_lru(candidates)

    logging.info(
        f"🧹 Non-priority eviction candidates | "
        f"priority_languages={sorted(priority_set)} | candidates={candidates}"
    )

    evicted_languages = []

    for language_code in candidates:
        current_pressure_gib = get_estimated_memory_pressure_gib()

        if is_pressure_back_to_safe_target(current_pressure_gib):
            break

        before_rss_gib = get_process_rss_gib()
        before_tmp_gib = get_tmp_used_gib()
        before_pressure_gib = current_pressure_gib

        evict_align_model_from_memory_and_disk(language_code, device=device)

        after_rss_gib = get_process_rss_gib()
        after_tmp_gib = get_tmp_used_gib()
        after_pressure_gib = get_estimated_memory_pressure_gib()

        evicted_languages.append(language_code)

        logging.info(
            f"🧹 Non-priority eviction step | lang={language_code} | "
            f"before_rss_gib={before_rss_gib:.2f} | after_rss_gib={after_rss_gib:.2f} | "
            f"before_tmp_gib={before_tmp_gib:.2f} | after_tmp_gib={after_tmp_gib:.2f} | "
            f"before_pressure_gib={before_pressure_gib:.2f} | "
            f"after_pressure_gib={after_pressure_gib:.2f}"
        )

    return evicted_languages


def evict_all_align_models(device: str) -> list[str]:
    candidates = sort_languages_by_lru(list(ALIGN_MODELS_CACHE.keys()))

    logging.warning(f"🚨 Full eviction candidates | candidates={candidates}")

    evicted_languages = []

    for language_code in candidates:
        current_pressure_gib = get_estimated_memory_pressure_gib()

        if is_pressure_back_to_safe_target(current_pressure_gib):
            break

        before_rss_gib = get_process_rss_gib()
        before_tmp_gib = get_tmp_used_gib()
        before_pressure_gib = current_pressure_gib

        evict_align_model_from_memory_and_disk(language_code, device=device)

        after_rss_gib = get_process_rss_gib()
        after_tmp_gib = get_tmp_used_gib()
        after_pressure_gib = get_estimated_memory_pressure_gib()

        evicted_languages.append(language_code)

        logging.warning(
            f"🚨 Full eviction step | lang={language_code} | "
            f"before_rss_gib={before_rss_gib:.2f} | after_rss_gib={after_rss_gib:.2f} | "
            f"before_tmp_gib={before_tmp_gib:.2f} | after_tmp_gib={after_tmp_gib:.2f} | "
            f"before_pressure_gib={before_pressure_gib:.2f} | "
            f"after_pressure_gib={after_pressure_gib:.2f}"
        )

    return evicted_languages


# =============================================================================
# ORCHESTRATION
# =============================================================================

def ensure_memory_headroom_for_alignment_cache(
    priority_languages: list[str],
    device: str = "cpu",
):
    pressure_before_gib = get_estimated_memory_pressure_gib()

    if not is_cleaning_required(pressure_before_gib):
        return

    log_memory_state("⚠️ Memory pressure high before cleaning")

    evicted_non_priority = evict_non_priority_align_models(
        priority_languages=priority_languages,
        device=device,
    )

    pressure_after_non_priority_gib = get_estimated_memory_pressure_gib()

    logging.info(
        f"🧹 Non-priority eviction result | "
        f"evicted={evicted_non_priority} | "
        f"pressure_after_gib={pressure_after_non_priority_gib:.2f}"
    )

    if is_pressure_back_to_safe_target(pressure_after_non_priority_gib):
        logging.info("✅ Back to safe zone after non-priority eviction")
        return

    evicted_all = evict_all_align_models(device=device)

    pressure_after_full_evict_gib = get_estimated_memory_pressure_gib()

    logging.warning(
        f"🚨 Full eviction result | "
        f"evicted={evicted_all} | "
        f"pressure_after_gib={pressure_after_full_evict_gib:.2f}"
    )

    if is_pressure_back_to_safe_target(pressure_after_full_evict_gib):
        logging.warning("🚨 Back to safe zone after full eviction")
        return

    cleanup_standard_disk_caches()

    log_memory_state("🧹 Final state after global disk cleanup")


# =============================================================================
# LOAD ALIGN MODEL
# =============================================================================
def get_or_load_align_model(language_code: str, device: str):
    language_code = normalize_language_code(language_code)

    if language_code in ALIGN_MODELS_CACHE:
        ALIGN_MODEL_LAST_USED_AT[language_code] = time.time()

        logging.info(f"🎯 Align cache HIT | lang={language_code}")

        return (
            ALIGN_MODELS_CACHE[language_code]["align_model"],
            ALIGN_MODELS_CACHE[language_code]["align_metadata"],
        )

    model_cache_dir = get_align_model_cache_dir(language_code)
    ensure_directory_exists(model_cache_dir)

    logging.info(
        f"🎯 Align cache MISS | lang={language_code} | model_cache_dir={model_cache_dir}"
    )

    try:
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language_code,
            device=device,
            model_dir=model_cache_dir,
        )
    except Exception as exc:
        logging.warning(
            f"⚠️ Align model load failed | lang={language_code} | "
            f"model_cache_dir={model_cache_dir} | error={exc}"
        )

        # important : éviter de conserver un cache partiel/corrompu
        safe_remove_path(model_cache_dir)
        ensure_directory_exists(model_cache_dir)

        raise

    ALIGN_MODELS_CACHE[language_code] = {
        "align_model": align_model,
        "align_metadata": align_metadata,
        "model_cache_dir": model_cache_dir,
    }

    ALIGN_MODEL_LAST_USED_AT[language_code] = time.time()

    log_memory_state(f"🎯 Align model loaded | lang={language_code}")

    return align_model, align_metadata
