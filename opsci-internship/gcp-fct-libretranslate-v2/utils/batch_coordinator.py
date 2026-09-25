import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from gcpOpsci.cloud_tasks import CloudTask, CloudTasks

if TYPE_CHECKING:
    from gcpOpsci.models import Document
    from gcpOpsci.tools import PerfTimer

# Garde-fous de durée (cf. docstring de translate_via_coordinator pour le détail du mécanisme).
# Cloud Run tue une requête sans préavis à `timeout_seconds` (functions.tf) — ces constantes
# doivent rester sous cette valeur. Toutes surchargeables par variable d'environnement, pour
# tester sans attendre une charge réelle de 15-28 min (effacé au prochain déploiement Terraform).

# Budget à partir duquel un DÉPOSANT peut voler le rôle de harvester au lieu de simplement
# déposer. Si personne ne relève, le harvester continue de travailler indéfiniment (pas de
# coupure ici, décision explicite).
HARVESTER_MAX_SECONDS = float(os.environ.get("HARVESTER_MAX_SECONDS", "900"))  # 15 min

# Deadline dure par harvester (dernier recours si jamais relevé) — 1600s, marge réelle sous
# `timeout_seconds` (1800s), pas une valeur théorique.
HARVESTER_DISPATCH_DEADLINE_SECONDS = float(os.environ.get("HARVESTER_DISPATCH_DEADLINE_SECONDS", "1600"))

# Filet de sécurité si même la deadline dure n'a pas pu s'exécuter (kill externe, OOM) : un
# harvester actif plus longtemps que ça est forcément mort sans nettoyage, on reprend le pool.
STALE_HARVESTER_SECONDS = float(os.environ.get("STALE_HARVESTER_SECONDS", "1740"))

# Plafonne une vague, pour garder des points de contrôle fréquents jusqu'à la deadline dure
# (calé sur un run réel, ~3000 docs/vague ≈ 70-80s).
MAX_DOCS_PER_WAVE = int(os.environ.get("MAX_DOCS_PER_WAVE", "3000"))


@dataclass
class _Pool:
    """Accumulateur partagé par clé `(target_lang, source_lang, force_translate)`.

    `harvester_generation` identifie quel harvester possède actuellement le rôle : le harvester
    en cours compare son propre numéro à celui du pool à chaque point de contrôle, et s'arrête
    proprement (sans toucher à l'état) s'ils diffèrent (relevé entre-temps).

    `doc_job_ids`/`in_flight_job_ids`/`job_callbacks` (2026-07-29) tracent, UNIQUEMENT pour les
    déposants qui fournissent `call_back_pipeline`, quel `parent_job_id` possède chaque doc_id —
    pour callback le pipeline d'origine dès que TOUS ses documents sont écrits, plutôt que de le
    faire poller l'état d'une Cloud Task déconnecté de la réalité (cf. README). `doc_job_ids`
    (en attente de traduction) et `in_flight_job_ids` (drainé, en cours de traduction/écriture par
    UN harvester, potentiellement pas celui qui vérifie) sont deux ensembles distincts : un job
    n'est "terminé" que si absent des DEUX — sans cette distinction, un harvester qui vérifie un
    job fusionné pendant qu'un AUTRE harvester (relève en cours) est encore en train d'écrire ses
    derniers documents conclurait à tort "terminé" (le pool est vide, mais l'écriture n'est pas
    encore faite)."""

    texts: List[str] = field(default_factory=list)
    doc_ids: List[int] = field(default_factory=list)
    detected_langs: Dict[int, str] = field(default_factory=dict)
    doc_tokens: Dict[int, List[str]] = field(default_factory=dict)
    n_skipped_detection_failed: int = 0
    n_fallback_en_unsupported_lang: int = 0
    harvester_active: bool = False
    harvester_started_at: Optional[float] = None
    harvester_generation: int = 0

    doc_job_ids: Dict[int, str] = field(default_factory=dict)
    in_flight_job_ids: Dict[int, str] = field(default_factory=dict)
    job_callbacks: Dict[str, dict] = field(default_factory=dict)


@dataclass
class CoordinatorOutcome:
    """Résultat pour LA requête appelante (pas pour l'ensemble fusionné, sauf role="harvester").

    - deposited : documents détectés puis pris en charge par un autre cycle — répondre sans écrire.
    - harvester : a traduit+écrit ce qu'elle a drainé (`n_written`/`n_docs`, potentiellement plus
      que ses propres documents, ou moins si relevée en route). `stats` agrège les compteurs de
      cache sur toutes ses vagues.
    - solo_error : détection échouée avant le pool — `doc_ids` = ceux de l'appelant, pour son retry.
    - harvester_error : traduction/écriture échouée en tant que harvester — `doc_ids` = tout le lot
      en échec (peut couvrir plusieurs requêtes d'origine), pour un retry qui couvre tout le monde.

    `own_n_detected` (2026-07-29) : nombre de documents détectés par CETTE requête (avant fusion) —
    si 0 alors qu'un `call_back_pipeline` a été fourni, ce job n'entre jamais dans le pool/dans
    aucune vague (rien à drainer, rien à écrire) : le mécanisme normal de callback (après écriture
    d'une vague) ne se déclenchera donc jamais pour lui. L'appelant (`main.py`) doit alors
    déclencher immédiatement le check own-job (`fire_callback_if_last`) sans attendre de vague.

    `doc_job_ids`/`job_callbacks_snapshot` : uniquement pour `harvester_error`, permettent à
    l'appelant de regrouper le retry par job d'origine (un lot en échec peut fusionner plusieurs
    jobs) plutôt que de tout recoller sous le job du harvester courant — sans ça, les autres jobs
    fusionnés perdraient silencieusement leur callback.
    """

    role: str
    n_written: int = 0
    n_docs: int = 0
    error: Optional[Exception] = None
    doc_ids: Optional[List[int]] = None
    stats: Optional[dict] = None
    own_n_detected: int = 0
    doc_job_ids: Optional[Dict[int, str]] = None
    job_callbacks_snapshot: Optional[Dict[str, dict]] = None


_pools: Dict[tuple, _Pool] = {}
_pools_lock = threading.Lock()


def _drain(pool: _Pool, max_docs: Optional[int] = None) -> _Pool:
    """À appeler sous `_pools_lock`. Capture le contenu de `pool` (ou au plus `max_docs`,
    laissant le reste pour une vague suivante) dans un nouvel objet, et vide `pool` d'autant.

    Les doc_ids drainés migrent de `pool.doc_job_ids` vers `pool.in_flight_job_ids` (toujours sur
    le pool persistant, jamais perdus) — `batch.doc_job_ids` n'est qu'une vue figée de ce que
    CETTE vague a pris, pour que l'appelant sache quels job_ids elle couvre une fois écrite."""
    if max_docs is None or len(pool.doc_ids) <= max_docs:
        batch_doc_job_ids = dict(pool.doc_job_ids)
        batch = _Pool(
            texts=pool.texts,
            doc_ids=pool.doc_ids,
            detected_langs=pool.detected_langs,
            doc_tokens=pool.doc_tokens,
            n_skipped_detection_failed=pool.n_skipped_detection_failed,
            n_fallback_en_unsupported_lang=pool.n_fallback_en_unsupported_lang,
            doc_job_ids=batch_doc_job_ids,
        )
        pool.texts, pool.doc_ids = [], []
        pool.detected_langs, pool.doc_tokens = {}, {}
        pool.n_skipped_detection_failed = 0
        pool.n_fallback_en_unsupported_lang = 0
        pool.in_flight_job_ids.update(batch_doc_job_ids)
        pool.doc_job_ids = {}
        return batch

    take_ids = set(pool.doc_ids[:max_docs])
    batch_doc_job_ids = {k: v for k, v in pool.doc_job_ids.items() if k in take_ids}
    batch = _Pool(
        texts=pool.texts[:max_docs],
        doc_ids=pool.doc_ids[:max_docs],
        detected_langs={k: v for k, v in pool.detected_langs.items() if k in take_ids},
        doc_tokens={k: v for k, v in pool.doc_tokens.items() if k in take_ids},
        n_skipped_detection_failed=pool.n_skipped_detection_failed,
        n_fallback_en_unsupported_lang=pool.n_fallback_en_unsupported_lang,
        doc_job_ids=batch_doc_job_ids,
    )
    pool.texts = pool.texts[max_docs:]
    pool.doc_ids = pool.doc_ids[max_docs:]
    pool.detected_langs = {k: v for k, v in pool.detected_langs.items() if k not in take_ids}
    pool.doc_tokens = {k: v for k, v in pool.doc_tokens.items() if k not in take_ids}
    pool.n_skipped_detection_failed = 0
    pool.n_fallback_en_unsupported_lang = 0
    pool.in_flight_job_ids.update(batch_doc_job_ids)
    pool.doc_job_ids = {k: v for k, v in pool.doc_job_ids.items() if k not in take_ids}
    return batch


def _merge_stats(acc: dict, extra: Optional[dict]) -> dict:
    if not extra:
        return acc
    for k in ("n_language_pairs", "n_cache_hits", "n_cache_misses"):
        acc[k] = acc.get(k, 0) + extra.get(k, 0)
    return acc


def _merge_back(pool: "_Pool", drained: "_Pool") -> None:
    """À appeler sous `_pools_lock`. Réinjecte un `_Pool` déjà drainé (ex. échec de
    re-planification) plutôt que de le perdre — une future requête le reprendra. Fait aussi
    repasser ses doc_ids de `in_flight_job_ids` à `doc_job_ids` (redevenus "en attente", plus
    "en cours d'écriture" puisque l'écriture n'a jamais eu lieu)."""
    pool.texts.extend(drained.texts)
    pool.doc_ids.extend(drained.doc_ids)
    pool.detected_langs.update(drained.detected_langs)
    pool.doc_tokens.update(drained.doc_tokens)
    pool.n_skipped_detection_failed += drained.n_skipped_detection_failed
    pool.n_fallback_en_unsupported_lang += drained.n_fallback_en_unsupported_lang
    for did in drained.doc_ids:
        pool.in_flight_job_ids.pop(did, None)
    pool.doc_job_ids.update(drained.doc_job_ids)


def _is_last_task_for_job(queue_name: str, job_id: str, is_own_job: bool) -> bool:
    """Généralise `_is_last_task_in_queue` (ex. `gcp-fct-translation/main.py`, ~12 copies dans le
    repo) : appel DIRECT à l'API Cloud Tasks (pas via `gcp-fct-tasks-status`), compte les tâches
    dont le nom contient `f"j{job_id}"`.

    Les usages existants supposent que le vérificateur est TOUJOURS l'une des tâches du job vérifié
    (d'où le seuil `<=1` — sa propre Cloud Task, encore "active" à ses propres yeux, compte dans le
    total). Ici, le harvester peut vérifier un job FUSIONNÉ qui n'est pas le sien (`is_own_job=
    False`) : sa propre Cloud Task ne contient jamais `j{job_id}` de cet autre job dans son nom,
    donc le seuil correct est `==0`, pas `<=1`. Fail-closed par construction (une erreur API
    laisse le job "non terminé" plutôt que de risquer un faux callback)."""
    import os as _os
    from google.cloud import tasks_v2

    project_id = _os.environ.get("GCLOUD_PROJECT", "demo-project")
    try:
        client = tasks_v2.CloudTasksClient()
        queue_path = client.queue_path(project_id, "europe-west1", queue_name)
        job_marker = f"j{job_id}"
        active_count = sum(
            1
            for task in client.list_tasks(
                request={"parent": queue_path, "response_view": tasks_v2.Task.View.BASIC}
            )
            if job_marker in task.name.split("/")[-1]
        )
        return active_count <= 1 if is_own_job else active_count == 0
    except Exception as e:
        logging.warning(f"⚠️ _is_last_task_for_job error (assume not last) | job_id={job_id} : {e}")
        return False


def _fire_pipeline_callback(call_back_pipeline: dict) -> None:
    """Identique au pattern déjà utilisé par `gcp-fct-translation` et une douzaine d'autres CF
    feuilles du repo — `task_name` fixe garantit la déduplication si plusieurs vérificateurs
    (harvesters concurrents lors d'une relève) se croient tous deux "derniers" pour le même job."""
    try:
        task = CloudTask(
            queue_name=call_back_pipeline["queue_name"],
            name=call_back_pipeline["task_name"],
            http_method="POST",
            url=call_back_pipeline["url"],
            payload=call_back_pipeline["payload"],
            include_oidc_token=True,
            timeout=1800,
        )
        CloudTasks().add_task(task)
    except Exception as e:
        if "already exists" in str(e).lower():
            logging.info(f"Pipeline callback already fired (dedup OK) | task_name={call_back_pipeline.get('task_name')}")
        else:
            logging.warning(f"⚠️ Pipeline callback error (non-fatal) | {e}")


def fire_callback_if_last(job_id: str, call_back_pipeline: dict, queue_name: str) -> None:
    """Utilisée par `main.py` pour le cas `own_n_detected == 0` : un job dont AUCUN document n'a
    pu être détecté n'entre jamais dans le pool, donc ne traverse jamais aucune vague — le check
    normal (après écriture) ne se déclenche jamais pour lui. Toujours `is_own_job=True` ici (c'est
    forcément le job de la requête appelante elle-même, jamais un job fusionné)."""
    if _is_last_task_for_job(queue_name, job_id, is_own_job=True):
        _fire_pipeline_callback(call_back_pipeline)


def translate_via_coordinator(
    documents: List["Document"],
    target_lang: str,
    source_lang: str,
    force_translate: bool,
    timer: "PerfTimer",
    db,
    detect_fn: Callable,
    translate_fn: Callable,
    write_fn: Callable,
    dispatch_remaining_fn: Callable[[List[int], Dict[int, str], Dict[str, dict]], None],
    parent_job_id: Optional[str] = None,
    call_back_pipeline: Optional[dict] = None,
    queue_name: Optional[str] = None,
) -> CoordinatorOutcome:
    """
    Fusionne détection+traduction+écriture de plusieurs requêtes concurrentes ciblant la même
    `(target_lang, source_lang, force_translate)` — cf. README pour le contexte.

    Design "pool partagé + harvester dynamique avec relève" :
    - Chaque requête détecte dans son propre thread (vrai parallélisme), puis dépose dans le pool.
    - Si aucun harvester actif : elle le devient. Si un harvester est actif depuis moins de
      `HARVESTER_MAX_SECONDS` : elle dépose et répond immédiatement (`role="deposited"`). Si actif
      depuis plus longtemps : elle **relève** — vole le rôle (`harvester_generation` incrémenté,
      budget réinitialisé à son propre départ). L'ancien harvester finit sa vague en cours puis
      s'arrête proprement à son prochain point de contrôle en constatant le changement.
    - Le harvester boucle par vagues de `MAX_DOCS_PER_WAVE` jusqu'à : être relevé (s'arrête, ne
      touche à rien), atteindre `HARVESTER_DISPATCH_DEADLINE_SECONDS` sans relève (dernier
      recours, dispatch d'une Cloud Task via `dispatch_remaining_fn` pour ce qu'il reste — évite
      de compter sur une requête organique future, `_pools` étant en mémoire du process), ou vider
      le pool (fin normale). Tant qu'il est relevé à temps, aucune limite intermédiaire ne
      l'arrête : il continue de travailler indéfiniment si personne ne prend le relais.

    Si le dispatch de dernier recours échoue, le contenu est réinjecté dans le pool (`_merge_back`)
    plutôt que perdu. `STALE_HARVESTER_SECONDS` est un filet distinct pour le cas où même ce
    dernier recours n'a pas pu s'exécuter (kill externe, OOM).

    Risque accepté (décision utilisateur) : un `role="deposited"` qui répond avant que ses
    documents soient réellement écrits perd le filet de sécurité Cloud Tasks. `text_fr`/`text_en`
    restent NULL dans ce cas, retraduits par un futur run sur la même importation — sauf pour un
    `just_translate_ids` ponctuel jamais rejoué.

    **Callback par job (2026-07-29)** : si `call_back_pipeline` est fourni, ses documents sont
    tagués `parent_job_id` dans le pool (`doc_job_ids`). Après chaque vague ÉCRITE (succès), pour
    chaque job distinct représenté dans cette vague : si plus aucun doc de ce job n'est en attente
    NI en cours d'écriture ailleurs (`in_flight_job_ids`, cf. docstring `_Pool` pour la race de
    relève que ça évite), et que `_is_last_task_for_job` confirme qu'aucune autre Cloud Task de ce
    job n'est encore active, le callback de ce job est déclenché — le pipeline appelant n'a alors
    plus besoin de poller `check_queue_status`.

    `detect_fn`/`translate_fn`/`write_fn`/`dispatch_remaining_fn` sont injectées pour éviter un
    import circulaire avec main.py.
    """
    key = (target_lang, source_lang, force_translate)

    try:
        detection = detect_fn(documents, target_lang, source_lang, force_translate)
    except Exception as e:
        return CoordinatorOutcome(role="solo_error", error=e, doc_ids=[d.id for d in documents])

    own_n_detected = len(detection.doc_ids)

    now = time.monotonic()
    with _pools_lock:
        pool = _pools.setdefault(key, _Pool())
        pool.texts.extend(detection.texts)
        pool.doc_ids.extend(detection.doc_ids)
        pool.detected_langs.update(detection.detected_langs)
        pool.doc_tokens.update(detection.doc_tokens)
        pool.n_skipped_detection_failed += detection.n_skipped_detection_failed
        pool.n_fallback_en_unsupported_lang += detection.n_fallback_en_unsupported_lang

        if call_back_pipeline is not None and parent_job_id is not None:
            for did in detection.doc_ids:
                pool.doc_job_ids[did] = parent_job_id
            pool.job_callbacks[parent_job_id] = call_back_pipeline

        if pool.harvester_active and pool.harvester_started_at is not None:
            active_since = now - pool.harvester_started_at
            if active_since > STALE_HARVESTER_SECONDS:
                logging.warning(
                    f"⚠️ Harvester bloqué détecté (actif depuis {active_since:.0f}s > "
                    f"{STALE_HARVESTER_SECONDS:.0f}s) pour target_lang={target_lang} — reprise "
                    f"forcée du pool, probable kill Cloud Run sans nettoyage."
                )
                pool.harvester_active = False

        if pool.harvester_active:
            active_since = now - (pool.harvester_started_at or now)
            if active_since >= HARVESTER_MAX_SECONDS:
                logging.info(
                    f"🔁 Relève du harvester (actif depuis {active_since:.0f}s >= "
                    f"{HARVESTER_MAX_SECONDS:.0f}s) pour target_lang={target_lang} — cette requête "
                    f"prend le relais."
                )
                pool.harvester_generation += 1
                pool.harvester_started_at = now
                my_generation = pool.harvester_generation
                batch = _drain(pool, max_docs=MAX_DOCS_PER_WAVE)
            else:
                logging.info(
                    f"🧩 Détecté et déposé (harvester déjà actif) | target_lang={target_lang} | "
                    f"documents={len(detection.doc_ids)}"
                )
                return CoordinatorOutcome(role="deposited", own_n_detected=own_n_detected)
        else:
            pool.harvester_active = True
            pool.harvester_started_at = now
            pool.harvester_generation += 1
            my_generation = pool.harvester_generation
            batch = _drain(pool, max_docs=MAX_DOCS_PER_WAVE)

    harvest_start = now
    total_written = 0
    total_docs = 0
    n_waves = 0
    stats_acc: dict = {}

    while batch.doc_ids:
        n_waves += 1
        wave_size = len(batch.doc_ids)
        try:
            translations, translate_stats = translate_fn(
                batch.texts, batch.doc_ids, batch.detected_langs, batch.doc_tokens, target_lang, timer,
            )
            n_written = write_fn(db, target_lang, translations, batch.detected_langs, timer)
        except Exception as e:
            job_callbacks_snapshot: Dict[str, dict] = {}
            with _pools_lock:
                # Ne réinitialise l'état que si on le possède encore (pas de relève entre-temps).
                if pool.harvester_generation == my_generation:
                    pool.harvester_active = False
                    pool.harvester_started_at = None
                # Snapshot des callbacks concernés par ce lot en échec (le retry, groupé par job
                # d'origine côté main.py, en a besoin — cf. CoordinatorOutcome.doc_job_ids).
                affected_jobs = set(batch.doc_job_ids.values())
                job_callbacks_snapshot = {
                    jid: cb for jid, cb in pool.job_callbacks.items() if jid in affected_jobs
                }
            return CoordinatorOutcome(
                role="harvester_error", error=e, doc_ids=batch.doc_ids, own_n_detected=own_n_detected,
                doc_job_ids=dict(batch.doc_job_ids), job_callbacks_snapshot=job_callbacks_snapshot,
            )

        _merge_stats(stats_acc, translate_stats)
        total_written += n_written
        total_docs += wave_size

        remaining: Optional[_Pool] = None
        relieved = False
        jobs_to_check: List[tuple] = []  # [(job_id, call_back_pipeline, is_own_job), ...]
        with _pools_lock:
            # La vague vient d'être écrite avec succès : ses doc_ids ne sont plus "in flight".
            for did in batch.doc_ids:
                pool.in_flight_job_ids.pop(did, None)
            for jid in set(batch.doc_job_ids.values()):
                still_pending = jid in pool.doc_job_ids.values() or jid in pool.in_flight_job_ids.values()
                if not still_pending and jid in pool.job_callbacks:
                    cb = pool.job_callbacks.pop(jid)
                    jobs_to_check.append((jid, cb, jid == parent_job_id))

            if pool.harvester_generation != my_generation:
                relieved = True
                batch = _Pool()
            else:
                elapsed = time.monotonic() - harvest_start
                if pool.doc_ids and elapsed < HARVESTER_DISPATCH_DEADLINE_SECONDS:
                    batch = _drain(pool, max_docs=MAX_DOCS_PER_WAVE)
                else:
                    if pool.doc_ids:
                        remaining = _drain(pool)
                    pool.harvester_active = False
                    pool.harvester_started_at = None
                    batch = _Pool()

        if relieved:
            logging.info(
                f"🔁 Relevé par un autre harvester (target_lang={target_lang}) — {total_docs} "
                f"documents traités avant relève, je rends la main."
            )

        # Hors verrou : _is_last_task_for_job/dispatch_remaining_fn font des appels réseau, ne
        # doivent pas bloquer le pool.
        for jid, cb, is_own in jobs_to_check:
            if _is_last_task_for_job(queue_name, jid, is_own_job=is_own):
                _fire_pipeline_callback(cb)
            else:
                # Pas encore vraiment fini côté Cloud Tasks (une autre tâche de ce job n'a pas
                # encore déposé) — remet le callback en attente pour une prochaine vérification.
                with _pools_lock:
                    pool.job_callbacks.setdefault(jid, cb)

        if remaining is not None:
            logging.warning(
                f"⚠️ Harvester a atteint la deadline dure ({elapsed:.0f}s >= "
                f"{HARVESTER_DISPATCH_DEADLINE_SECONDS:.0f}s) sans relève, avec "
                f"{len(remaining.doc_ids)} documents encore en attente pour "
                f"target_lang={target_lang} — re-planification d'une Cloud Task dédiée "
                f"(dernier recours)."
            )
            try:
                dispatch_remaining_fn(remaining.doc_ids, remaining.doc_job_ids, dict(pool.job_callbacks))
            except Exception as e:
                logging.error(
                    f"🚨 Échec de re-planification pour {len(remaining.doc_ids)} documents restants "
                    f"(target_lang={target_lang}) : {e}. Réinjectés dans le pool — repris "
                    f"passivement par une future requête en dernier recours."
                )
                with _pools_lock:
                    _merge_back(pool, remaining)

    if n_waves > 1 or total_docs > len(documents):
        logging.info(
            f"🧩 Traduction fusionnée (harvester) | target_lang={target_lang} | "
            f"vagues={n_waves} | documents={total_docs} | dont propres={len(documents)}"
        )

    return CoordinatorOutcome(
        role="harvester", n_written=total_written, n_docs=total_docs, stats=stats_acc,
        own_n_detected=own_n_detected,
    )
