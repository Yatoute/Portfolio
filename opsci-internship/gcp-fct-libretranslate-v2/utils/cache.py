import gc
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Optional


# =============================================================================
# CONFIGURATION MEMOIRE (mêmes seuils/logique que gcp-fct-video-transcription/utils/cache.py)
# =============================================================================

DEFAULT_ALLOCATED_MEMORY = "14Gi"
DEFAULT_RESERVED_AVAILABLE_MEMORY = "3Gi"
DEFAULT_CLEANING_MARGIN = "1Gi"

RAW_ALLOCATED_MEMORY = os.environ.get("ALLOCATED_MEMORY") or DEFAULT_ALLOCATED_MEMORY
RAW_RESERVED_AVAILABLE_MEMORY = (
    os.environ.get("MIN_AVAILABLE_MEMORY") or DEFAULT_RESERVED_AVAILABLE_MEMORY
)
RAW_CLEANING_MARGIN = os.environ.get("CLEANING_MARGIN") or DEFAULT_CLEANING_MARGIN

# Langue pivot utilisée par Argos Translate quand aucun paquet direct n'existe pour une paire
# (ex. ro->fr n'existe pas dans l'index Argos, seulement ro->en et en->fr).
PIVOT_LANG = "en"


def parse_size_to_gib(value: Optional[str | int | float]) -> float:
    if value is None:
        raise ValueError("Memory size value cannot be None")

    if isinstance(value, (int, float)):
        return float(value)

    raw_value = str(value).strip().lower()

    units = {
        "gib": 1.0, "gi": 1.0, "gb": 1.0, "g": 1.0,
        "mib": 1.0 / 1024.0, "mi": 1.0 / 1024.0, "mb": 1.0 / 1024.0, "m": 1.0 / 1024.0,
        "kib": 1.0 / (1024.0 ** 2), "ki": 1.0 / (1024.0 ** 2), "kb": 1.0 / (1024.0 ** 2), "k": 1.0 / (1024.0 ** 2),
        "b": 1.0 / (1024.0 ** 3),
    }

    for suffix, factor in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if raw_value.endswith(suffix):
            numeric_part = raw_value[: -len(suffix)].strip()
            return float(numeric_part) * factor

    return float(raw_value)


ALLOCATED_MEMORY_GIB = parse_size_to_gib(RAW_ALLOCATED_MEMORY)
RESERVED_AVAILABLE_MEMORY_GIB = parse_size_to_gib(RAW_RESERVED_AVAILABLE_MEMORY)
CLEANING_MARGIN_GIB = parse_size_to_gib(RAW_CLEANING_MARGIN)


# =============================================================================
# CACHE GLOBAL PAR PAQUET (from_code, to_code)
# =============================================================================
# Granularité = paquet Argos (saut unique), pas la paire de traduction demandée : un pivot
# ro->fr partage le paquet en->fr avec par exemple de->fr. L'éviction doit donc se faire au
# niveau du paquet, sous peine de casser une autre paire encore utile.
PACKAGE_LAST_USED_AT: dict[tuple[str, str], float] = {}

# Protège PACKAGE_LAST_USED_AT + les décisions d'éviction/installation LRU (2026-07-27, cf.
# batch_coordinator.py) : sans concurrency par instance, un seul thread appelait jamais ce module
# en même temps. Avec max_instance_request_concurrency > 1, plusieurs cycles de fusion (ex. fr et
# en en parallèle) peuvent appeler get_or_load_translation/evict_package/prefetch_packages en même
# temps — sans verrou, une éviction LRU décidée par un thread pourrait interrompre une lecture de
# state en cours dans un autre. Volontairement gardé COURT (comptabilité + décision uniquement) :
# le téléchargement réseau lui-même (_ensure_package_installed) reste hors du verrou pour ne pas
# sérialiser les installations de paquets différents et annuler le bénéfice de la concurrency.
# Limite connue, non couverte ici : un paquet activement utilisé par un translate_batch() en cours
# dans un thread n'est pas explicitement "épinglé" contre l'éviction décidée par un autre thread
# pendant ce temps — risque restreint en pratique (paquets ~40-80 Mo, marge mémoire 16Gi,
# concurrency de traduction bornée par ARGOS_INTER_THREADS=4), mais pas formellement exclu.
# RLock (pas Lock) : evict_non_priority_packages tient le verrou en appelant evict_package, qui
# le reprend lui-même — un Lock simple ferait un deadlock sur ce ré-entrant.
_PACKAGE_STATE_LOCK = threading.RLock()

# Throttle GLOBAL (tout le process, tous cycles de fusion confondus) sur le nombre d'installations
# de paquets Argos simultanées — cf. docstring de _ensure_package_installed pour le contexte
# complet (contention observée le 2026-07-27, Test 10 : jusqu'à 24 installs concurrentes sur une
# instance à 4 CPU quand plusieurs cycles fusionnés lancent chacun leur propre rafale). Calé sur
# le nombre de CPU de l'instance (available_cpu=4 dans functions.tf) : l'installation fait du
# travail CPU-bound (dézippage, vérification MD5), pas seulement du réseau — au-delà de 4 en
# parallèle sur 4 CPU, on ajoute de la contention sans gagner en débit réel.
_GLOBAL_INSTALL_MAX_CONCURRENT = 4
_GLOBAL_INSTALL_SEMAPHORE = threading.Semaphore(_GLOBAL_INSTALL_MAX_CONCURRENT)

# Sérialise l'ENTRÉE dans _exclusive_of_installs() (2026-07-28, incident prod : deadlock confirmé
# entre 8 requêtes concurrentes de translate_chunks, cf. README). Sans ce verrou, deux threads
# peuvent chacun acquérir une partie des 4 jetons du sémaphore en entrelaçant leurs boucles
# d'acquisition, puis attendre indéfiniment le reste — détenu par l'autre thread, bloqué de façon
# symétrique. Avec ce verrou, un seul thread à la fois tente de vider le sémaphore ; les autres
# attendent leur tour au lieu de se disputer des jetons partiels.
_EXCLUSIVE_OF_INSTALLS_LOCK = threading.Lock()


@contextmanager
def _exclusive_of_installs():
    """Barrière d'exclusion vis-à-vis de TOUTE installation en cours, n'importe où dans le
    process (2026-07-27) : `_GLOBAL_INSTALL_SEMAPHORE` limite le nombre d'installations
    concurrentes, mais ne les empêche pas de tourner PENDANT qu'un autre thread scanne les paquets
    installés (`get_translation_from_codes()`, qui rescanne TOUT, pas seulement la paire demandée).

    Technique du "lecteur exclusif" via un sémaphore à compteur : prendre les
    `_GLOBAL_INSTALL_MAX_CONCURRENT` jetons d'un coup revient à s'assurer qu'aucune installation
    (qui en détient toujours au moins 1 pendant qu'elle tourne) n'est active. `_EXCLUSIVE_OF_INSTALLS_LOCK`
    protège cette acquisition elle-même (cf. commentaire au-dessus) — sans lui, deux appelants
    concurrents de cette fonction peuvent se bloquer mutuellement à vie."""
    with _EXCLUSIVE_OF_INSTALLS_LOCK:
        for _ in range(_GLOBAL_INSTALL_MAX_CONCURRENT):
            _GLOBAL_INSTALL_SEMAPHORE.acquire()
        try:
            yield
        finally:
            for _ in range(_GLOBAL_INSTALL_MAX_CONCURRENT):
                _GLOBAL_INSTALL_SEMAPHORE.release()


def normalize_code(code: str) -> str:
    return code.strip().lower()


def ensure_argos_env_configured() -> None:
    """À appeler avant le premier import de argostranslate (ses settings sont résolus au
    chargement du module, donc dans le worker après fork Gunicorn, pas au niveau module)."""
    import ctranslate2

    os.environ.setdefault(
        "ARGOS_PACKAGES_DIR", os.environ.get("ARGOS_PACKAGES_DIR", "/tmp/argos_packages")
    )
    os.environ.setdefault(
        "ARGOS_DEVICE_TYPE", "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    )
    os.environ.setdefault("ARGOS_COMPUTE_TYPE", "int8")
    # ARGOS_BEAM_SIZE : on garde le défaut d'argostranslate (4) pour rester fidèle au
    # comportement de gcp-fct-translation (LibreTranslate) plutôt que de le modifier.

    # ARGOS_BATCH_SIZE (nombre de tokens par lot interne CTranslate2, cf. batch_type="tokens"
    # dans main.py::_batch_translate_sentences) : relevé du défaut Argos (32, pensé pour du
    # matériel modeste). Historique des paliers testés sur le GPU L4 de prod : 256 (Test 4, ~2%
    # de gain seulement) -> 1024 (Test 5, ×5,78 vs Test 4 — bien au-delà de la prédiction locale
    # ×2,3 mesurée sur un GPU portable, RTX 4050) -> 2048 (Test 6, ×1,12 vs Test 5 seulement —
    # rendement net décroissant, signe qu'un plateau approche, à un palier plus élevé que celui
    # observé en local à 1024). Passé à 4096 pour le Test 7 dans le seul but de CONFIRMER ce
    # plateau (pas d'attente de gain significatif) — si le Test 7 ne montre quasiment aucune
    # amélioration (ou une dégradation) par rapport au Test 6 (27,37 ms/doc, 0,405379$), revenir
    # à 2048 comme valeur définitive.
    os.environ.setdefault("ARGOS_BATCH_SIZE", "4096")

    # argostranslate.utils.info() logge à INFO (inconditionnellement, pas juste en debug)
    # chaque étape interne (sentences/tokenized/translated_batches/...) avec le texte complet
    # de chaque phrase — bruit + volume Cloud Logging inutiles. Son logger est nommé
    # "argostranslate.utils" (logging.getLogger(__name__) dans argostranslate/utils.py),
    # distinct du nôtre : on peut le baisser sans toucher à nos propres logs applicatifs.
    logging.getLogger("argostranslate.utils").setLevel(logging.WARNING)


# =============================================================================
# METRIQUES MEMOIRE
# =============================================================================

def get_process_rss_gib() -> float:
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss / (1024 ** 3)


def get_tmp_used_gib() -> float:
    _, used_bytes, _ = shutil.disk_usage("/tmp")
    return used_bytes / (1024 ** 3)


def get_estimated_memory_pressure_gib() -> float:
    return get_process_rss_gib() + get_tmp_used_gib()


def get_cleaning_threshold_gib() -> float:
    return max(0.0, ALLOCATED_MEMORY_GIB - RESERVED_AVAILABLE_MEMORY_GIB)


def get_target_pressure_after_cleaning_gib() -> float:
    return max(0.0, ALLOCATED_MEMORY_GIB - RESERVED_AVAILABLE_MEMORY_GIB - CLEANING_MARGIN_GIB)


def is_cleaning_required(current_pressure_gib: float) -> bool:
    return current_pressure_gib >= get_cleaning_threshold_gib()


def is_pressure_back_to_safe_target(current_pressure_gib: float) -> bool:
    return current_pressure_gib <= get_target_pressure_after_cleaning_gib()


def log_memory_state(prefix: str) -> None:
    logging.info(
        f"{prefix} | rss_gib={get_process_rss_gib():.2f} | tmp_used_gib={get_tmp_used_gib():.2f} | "
        f"pressure_gib={get_estimated_memory_pressure_gib():.2f} | "
        f"threshold_gib={get_cleaning_threshold_gib():.2f}"
    )


# =============================================================================
# EVICTION LRU DES PAQUETS ARGOS
# =============================================================================

def _installed_package_for(from_code: str, to_code: str):
    import argostranslate.package

    for pkg in argostranslate.package.get_installed_packages():
        if pkg.from_code == from_code and pkg.to_code == to_code:
            return pkg
    return None


def sort_packages_by_lru(package_keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(package_keys, key=lambda k: PACKAGE_LAST_USED_AT.get(k, 0.0))


def evict_package(from_code: str, to_code: str) -> None:
    """Désinstalle un paquet Argos : supprime la référence globale au Translator CTranslate2
    (Argos la garde en mémoire dans translate.installed_translates, uninstall() seul ne
    suffit pas à libérer la RAM) puis le paquet sur disque (/tmp, compté dans la RAM du
    conteneur sur Cloud Run)."""
    import argostranslate.package
    import argostranslate.translate

    key = (from_code, to_code)
    with _PACKAGE_STATE_LOCK:
        PACKAGE_LAST_USED_AT.pop(key, None)

        pkg = _installed_package_for(from_code, to_code)
        if pkg is None:
            return

        package_key = f"{from_code}-{to_code}"
        argostranslate.translate.installed_translates[:] = [
            it
            for it in argostranslate.translate.installed_translates
            if it.package_key != package_key
        ]

        try:
            argostranslate.package.uninstall(pkg)
            logging.info(f"🧹 Paquet Argos désinstallé | pair={key}")
        except Exception as exc:
            logging.warning(f"⚠️ Échec désinstallation paquet | pair={key} | error={exc}")

        argostranslate.translate.get_installed_languages.cache_clear()
    gc.collect()


def evict_non_priority_packages(priority_keys: set[tuple[str, str]]) -> list[tuple[str, str]]:
    with _PACKAGE_STATE_LOCK:
        candidates = [k for k in list(PACKAGE_LAST_USED_AT.keys()) if k not in priority_keys]
        candidates = sort_packages_by_lru(candidates)

        evicted: list[tuple[str, str]] = []
        for key in candidates:
            if is_pressure_back_to_safe_target(get_estimated_memory_pressure_gib()):
                break
            evict_package(*key)
            evicted.append(key)
        return evicted


def ensure_memory_headroom(priority_keys: set[tuple[str, str]]) -> None:
    pressure_before_gib = get_estimated_memory_pressure_gib()
    if not is_cleaning_required(pressure_before_gib):
        return

    log_memory_state("⚠️ Pression mémoire élevée avant nettoyage")
    evicted = evict_non_priority_packages(priority_keys)
    log_memory_state(f"🧹 Après éviction non-prioritaire | evicted={evicted}")


# =============================================================================
# CHARGEMENT A LA DEMANDE D'UNE TRADUCTION (direct ou pivot via l'anglais)
# =============================================================================

def _install_from_gcs(from_code: str, to_code: str) -> bool:
    """
    Tente de récupérer le paquet (from_code, to_code) depuis notre miroir GCS
    (`gs://.../ARGOS_MODELS/{from_code}_{to_code}/`, peuplé/rafraîchi via
    `notebooks/maintenance/upload_modeles_argos_translate.ipynb`) plutôt que depuis le CDN
    public `argos-net.com`.

    Transfert same-region (rapide, sans dépendance réseau externe) et version figée (contrairement
    au CDN public, aucune dérive de modèle silencieuse possible — cf. root cause du bug tout-caps
    de LibreTranslate v1, masqué par un cache mémoire dont la version installée n'était plus
    connue). Retourne False (sans lever d'exception) si le paquet n'est pas dans le miroir — la
    couverture initiale ne concerne que les langues d'ALLOWED_LANGUAGES au moment de la synchro,
    l'appelant se rabat alors sur le CDN public.
    """
    try:
        from gcpOpsci.cloud_storage import Bucket
        import argostranslate.settings
    except ImportError:
        return False

    bucket_name = os.environ.get("GCP_BUCKET_NAME", "demo-project.appspot.com")
    gcp_models_dir = os.environ.get("ARGOS_MODELS_GCS_DIR", "ARGOS_MODELS")
    gcp_prefix = f"{gcp_models_dir}/{from_code}_{to_code}"

    try:
        bucket = Bucket(bucket_name)
        if not bucket.check_if_file_exist(gcp_prefix):
            return False

        local_dir = os.path.join(
            argostranslate.settings.package_data_dir, f"{from_code}_{to_code}"
        )
        bucket.download_directory(gcp_prefix, local_dir)
    except Exception as e:
        logging.warning(
            f"⚠️ Échec de récupération du paquet depuis GCS (repli sur le CDN public) | "
            f"pair=({from_code}, {to_code}) | error={e}"
        )
        return False

    return os.path.isfile(os.path.join(local_dir, "metadata.json"))


def _ensure_package_installed(from_code: str, to_code: str) -> bool:
    """Installe le paquet Argos (from_code, to_code) s'il n'est pas déjà présent sur disque.
    Retourne True si le paquet est disponible (déjà installé ou installé avec succès).

    L'installation elle-même (téléchargement + écriture disque) passe par
    `_GLOBAL_INSTALL_SEMAPHORE` (2026-07-27, cf. README section fusion) : observé en prod (Test 10,
    concurrency=8) — plusieurs cycles de fusion concurrents lancent chacun leur PROPRE rafale
    `prefetch_packages()` (son propre `ThreadPoolExecutor`), et sur une instance à froid ça peut
    donner jusqu'à 4 cycles × 6 paquets = 24 installations simultanées sur une instance à 4 CPU —
    contention sévère (requêtes à 70-90s observées). Le sémaphore est GLOBAL au process (pas par
    cycle, pas par appel à prefetch_packages) : peu importe le nombre de cycles concurrents ou le
    chemin d'appel (prefetch_packages, get_or_load_translation direct, retry MD5), au plus
    `_GLOBAL_INSTALL_MAX_CONCURRENT` installations tournent en même temps dans tout le process. Ne
    protège que l'installation (réseau + disque), pas la vérification "déjà installé ?" ci-dessus
    (rapide, pas de raison de la faire attendre)."""
    import argostranslate.package

    key = (from_code, to_code)
    if _installed_package_for(from_code, to_code) is not None:
        PACKAGE_LAST_USED_AT[key] = time.time()
        return True

    with _GLOBAL_INSTALL_SEMAPHORE:
        if _install_from_gcs(from_code, to_code):
            PACKAGE_LAST_USED_AT[key] = time.time()
            log_memory_state(f"☁️ Paquet Argos installé depuis le miroir GCS | pair={key}")
            return True

        logging.info(
            f"⬇️ Installation du paquet Argos depuis argos-net.com (absent du miroir GCS) | pair={key}"
        )
        installed = argostranslate.package.install_package_for_language_pair(from_code, to_code)
        if installed:
            PACKAGE_LAST_USED_AT[key] = time.time()
            log_memory_state(f"⬇️ Paquet Argos installé | pair={key}")
        return installed


def _resolve_needed_package_keys(from_code: str, to_code: str) -> list[tuple[str, str]]:
    """Détermine le(s) paquet(s) Argos nécessaire(s) pour (from_code, to_code) : direct si déjà
    installé, sinon pivot via l'anglais (deux paquets). Factorisé hors de
    `get_or_load_translation()` pour être appelable en amont par `prefetch_packages()`, avant
    de savoir laquelle des langues d'un batch aura besoin de quel(s) paquet(s).

    Limite connue (inchangée par cette extraction) : ne consulte que les paquets déjà
    INSTALLÉS localement, pas l'index public Argos — un paquet direct existant mais jamais
    encore installé (ex. pt<->es, seule paire non-anglaise connue de l'index, cf. README) sera
    donc traité comme un pivot tant qu'il n'a jamais été installé directement au moins une fois."""
    if _installed_package_for(from_code, to_code) is not None:
        return [(from_code, to_code)]
    if PIVOT_LANG in (from_code, to_code):
        # Un des deux est déjà la langue pivot : pas de pivot possible au-delà, seul le
        # paquet direct (s'il existe dans l'index) peut répondre.
        return [(from_code, to_code)]
    return [(from_code, PIVOT_LANG), (PIVOT_LANG, to_code)]


def evict_translation_packages(from_code: str, to_code: str) -> None:
    """Force l'éviction de tous les paquets nécessaires pour (from_code, to_code) — direct ou
    les deux jambes du pivot — pour forcer un réinstall propre au prochain appel de
    `get_or_load_translation()`.

    Filet de sécurité (2026-07-26) pour un paquet déjà installé mais dont Stanza rejette le
    contenu (échec de vérification MD5 à l'initialisation de `stanza.Pipeline()`, cf. README) :
    root cause pas formellement confirmée (probable bug de cache interne à Stanza en cas de
    chargement de plusieurs pipelines de langues différentes dans le même process, cf.
    stanfordnlp/stanza#213 — une race condition documentée sur son fichier resources.json, pas
    exactement notre cas mais même famille de symptôme), donc mitigation pragmatique plutôt que
    correctif de fond : un réinstall propre suffit à repartir sur un état sain la fois suivante.
    """
    for key in _resolve_needed_package_keys(normalize_code(from_code), normalize_code(to_code)):
        evict_package(*key)


def prefetch_packages(target_lang: str, source_langs: list[str]) -> dict[str, bool]:
    """
    Précharge EN PARALLÈLE tous les paquets Argos nécessaires pour traduire un ensemble de
    langues sources détectées vers `target_lang`, avant que la boucle de traduction séquentielle
    (cf. `process_translation` dans main.py) ne les demande une par une.

    Pourquoi : appeler `get_or_load_translation()` en boucle séquentielle par langue fait
    attendre la traduction de la 2e langue la fin du téléchargement de la 1re, alors que ce sont
    des transferts réseau/disque indépendants (miroir GCS ou CDN Argos) qui peuvent tourner en
    parallèle sans conflit — confirmé en lisant `argostranslate/package.py` : l'écriture du
    catalogue installé (`install_from_path`) est protégée par un verrou global
    (`argostranslate.package.package_lock`), mais le TÉLÉCHARGEMENT lui-même se fait hors de ce
    verrou, donc réellement parallélisable. Le dédoublonnage sur `(from_code, to_code)` avant
    dispatch évite tout risque de télécharger deux fois le même paquet en parallèle (un paquet
    pivot `en->target_lang` est potentiellement partagé par plusieurs langues sources du même
    batch).

    Retourne `{source_lang: was_cache_hit}` (True si TOUS les paquets nécessaires pour cette
    langue étaient déjà installés avant cet appel) — à réutiliser tel quel pour les stats de
    cache de l'appelant : une fois le préchargement fait, `get_or_load_translation()` verrait
    toujours un cache hit après coup et ne peut plus servir à mesurer le taux de cache réel.
    """
    ensure_argos_env_configured()

    needed_by_lang: dict[str, list[tuple[str, str]]] = {}
    all_needed: set[tuple[str, str]] = set()
    for lang in source_langs:
        lang = normalize_code(lang)
        if lang == normalize_code(target_lang):
            continue
        keys = _resolve_needed_package_keys(lang, normalize_code(target_lang))
        needed_by_lang[lang] = keys
        all_needed.update(keys)

    if not all_needed:
        return {}

    cache_hit_by_lang = {
        lang: all(_installed_package_for(*key) is not None for key in keys)
        for lang, keys in needed_by_lang.items()
    }

    ensure_memory_headroom(priority_keys=all_needed)

    missing = [key for key in all_needed if _installed_package_for(*key) is None]
    if missing:
        logging.info(f"⬇️ Préchargement parallèle de {len(missing)} paquet(s) Argos | {missing}")
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as executor:
            futures = {executor.submit(_ensure_package_installed, *key): key for key in missing}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    if not future.result():
                        logging.warning(f"⚠️ Échec de préchargement du paquet Argos | pair={key}")
                except Exception as exc:
                    logging.warning(
                        f"⚠️ Échec de préchargement du paquet Argos | pair={key} | error={exc}"
                    )

    with _PACKAGE_STATE_LOCK:
        for key in all_needed:
            PACKAGE_LAST_USED_AT[key] = time.time()

    return cache_hit_by_lang


def get_or_load_translation(from_code: str, to_code: str) -> tuple:
    """Retourne (ITranslation, was_cache_hit) pour (from_code, to_code).

    Installe à la demande le(s) paquet(s) nécessaire(s) — direct si disponible dans l'index
    Argos, sinon pivot via l'anglais (deux paquets) — et garde en cache LRU (mémoire + disque)
    uniquement les paquets utilisés récemment. `from_code` et `to_code` doivent être différents
    (le cas identité est géré par l'appelant).

    `was_cache_hit` est True seulement si TOUS les paquets nécessaires étaient déjà installés
    avant cet appel (utile pour instrumenter le taux de cache dans PerfTimer) — n'a plus ce sens
    une fois `prefetch_packages()` déjà passé par là (verra un cache hit après coup) : dans ce
    cas l'appelant doit se fier à la valeur retournée par `prefetch_packages()` à la place.
    """
    ensure_argos_env_configured()
    import argostranslate.translate

    from_code = normalize_code(from_code)
    to_code = normalize_code(to_code)

    needed_keys = _resolve_needed_package_keys(from_code, to_code)
    was_cache_hit = all(_installed_package_for(*key) is not None for key in needed_keys)

    ensure_memory_headroom(priority_keys=set(needed_keys))

    missing = [key for key in needed_keys if not _ensure_package_installed(*key)]
    if missing:
        raise ValueError(
            f"Aucun paquet Argos Translate disponible pour {from_code} -> {to_code} "
            f"(paquet(s) manquant(s) : {missing})."
        )

    # Sous verrou + barrière d'exclusion vis-à-vis des installations en cours (2026-07-27, cf.
    # README section fusion, 2 correctifs successifs) : `get_installed_languages.cache_clear()`
    # invalide un cache PARTAGÉ par tout le process, et `get_translation_from_codes()` le
    # reconstruit en RESCANNANT tous les paquets installés — pas seulement ceux demandés ici.
    # `_PACKAGE_STATE_LOCK` seul empêchait deux scans de se marcher dessus, mais PAS un scan de
    # tomber pendant qu'un AUTRE paquet (n'importe lequel) est encore en cours d'installation sous
    # `_GLOBAL_INSTALL_SEMAPHORE` — récidive confirmée en prod après le 1er fix (`'Package' object
    # has no attribute 'tokenizer'` sur une langue dont le propre paquet était déjà prêt, pendant
    # que d'autres finissaient de s'installer). `_exclusive_of_installs()` bloque jusqu'à ce
    # qu'AUCUNE installation ne soit active nulle part avant de scanner. Le téléchargement lui-même
    # (plus haut, sous le seul sémaphore) reste hors de cette barrière pour rester parallélisable.
    with _exclusive_of_installs(), _PACKAGE_STATE_LOCK:
        argostranslate.translate.get_installed_languages.cache_clear()
        translation = argostranslate.translate.get_translation_from_codes(from_code, to_code)
        if translation is None:
            raise ValueError(f"Impossible de construire une traduction {from_code} -> {to_code}.")

        for key in needed_keys:
            PACKAGE_LAST_USED_AT[key] = time.time()

    return translation, was_cache_hit
