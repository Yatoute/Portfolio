import re
import json
import logging
import time
import datetime
from dataclasses import dataclass
from typing import Any, Optional, List, Dict, Tuple

from bs4 import BeautifulSoup
import emoji
from flask import Request
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from sqlalchemy import update, func, case, text
from sqlalchemy.exc import OperationalError
from gcpOpsci.db import get_db
from gcpOpsci.models import Post, Document, PostDocumentAssociation, Chunk
from gcpOpsci.cloud_tasks import CloudTasks, CloudTask
from gcpOpsci.tools import log_and_return_response, make_task_id, PerfTimer

# --- Import de fast-langdetect pour la détection de langue (identique à gcp-fct-translation) ---
from fast_langdetect import detect

# Amorçage du modèle fasttext dès l'import (phase single-thread garantie du cold start), avant
# que Cloud Run ne dispatche des requêtes concurrentes (max_instance_request_concurrency=8) : le
# téléchargement du modèle (fast_langdetect/infer.py::ModelDownloader.download) écrit en mode
# append SANS verrou ni fichier temporaire — plusieurs threads qui le déclenchent en même temps
# corrompent le fichier local de façon permanente (jamais revalidé ni re-téléchargé ensuite),
# provoquant un échec de détection systématique pour toute la durée de vie de l'instance
# (incident constaté le 2026-07-30). Un seul appel ici suffit : le modèle chargé est mis en cache
# par un singleton module-level de fast_langdetect, partagé par tous les threads du process.
try:
    detect("warmup", model="auto", k=1)
except Exception as e:
    logging.warning(f"⚠️ Amorçage du modèle fast-langdetect échoué (retenté à la 1ʳᵉ requête) : {e}")

from utils.cache import get_or_load_translation, prefetch_packages, evict_translation_packages, PIVOT_LANG
from utils.batch_coordinator import translate_via_coordinator, fire_callback_if_last

SERVICE_URL = "https://europe-west1-demo-project.cloudfunctions.net/libretranslate-v2"
TASK_TIMEOUT_SECONDS = 1800

# --- Langues supportées : toutes celles couvertes par un paquet Argos Translate `->en`
# (le pivot passe toujours par l'anglais, cf. PIVOT_LANG dans utils/cache.py), plus l'anglais
ALLOWED_LANGUAGES = {
    "ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et", "eu",
    "fa", "fi", "fr", "ga", "gl", "he", "hi", "hu", "id", "it", "ja", "ko", "ky", "lt",
    "lv", "nb", "nl", "pb", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv",
    "th", "tr", "uk", "ur", "vi", "zh", "zt",
}

# --- Pré-compilation des expressions régulières pour le nettoyage avant détection ---
_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\w+")
_REPEATED_PUNCT_RE = re.compile(r"([^\w\s])\1+")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_text_for_lang_detection(text: str) -> str:
    """Nettoie le texte avant détection de langue : URLs, emojis, hashtags,
    ponctuations répétées et espaces consécutifs."""
    text = _URL_RE.sub(" ", text)
    text = _HASHTAG_RE.sub(" ", text)
    text = emoji.replace_emoji(text, replace=" ")
    text = _REPEATED_PUNCT_RE.sub(r"\1", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def detect_language(text: str) -> str:
    """Détecte la langue d'un texte nettoyé via fast-langdetect."""
    cleaned = clean_text_for_lang_detection(text)
    result = detect(cleaned, model="auto", k=1)
    return result[0]["lang"].lower()


# --- Pré-compilation des expressions régulières pour la protection des entités ---
MENTION_PATTERN = r"(?:RT\s|QT\s)?(?<=^|(?<=[^a-zA-Z0-9-_\.]))(@[A-Za-z0-9_]{4,15})"
HASHTAG_PATTERN = r"(\B#\w+)"
URL_PATTERN = r"(https?://[^ ]+)"
EMOJI_PATTERN = r":[a-zA-Z_]+:"
TRANSLATE_REGEX = re.compile(
    EMOJI_PATTERN + "|" + MENTION_PATTERN + "|" + HASHTAG_PATTERN + "|" + URL_PATTERN
)


def remove_html_tags(text: str) -> str:
    """Supprime les tags HTML d'un texte."""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()


def protect_entities(text: str) -> Tuple[str, List[str]]:
    """
    Protège mentions/hashtags/urls/emojis avant traduction en les remplaçant par des
    placeholders en texte brut (ex. "PLACEHOLDER0"), à restaurer après coup via
    restore_entities().

    Validé empiriquement le 2026-07-23 (notebooks/perfs/perfs_translate) sur le pivot
    ro->en->fr réellement utilisé en prod : contrairement à un encodage HTML +
    `translate_html` (testé en prod puis réverté — ×3,5-4,5 plus lent car il fragmente
    le texte en un appel de traduction par entité), un simple remplacement en texte brut
    ne coûte que ~+20% (un seul appel `.translate()`, séquence juste un peu plus longue).
    Pas garanti à 100% (le modèle peut occasionnellement perdre un placeholder sur un
    pivot), mais un échec isolé ne dégrade que l'entité concernée, pas tout le document.
    """
    text_demojized = emoji.demojize(text)
    tokens: List[str] = []

    def replace_with_placeholder(match: re.Match) -> str:
        tokens.append(match.group(0))
        return f" PLACEHOLDER{len(tokens) - 1} "

    protected = TRANSLATE_REGEX.sub(replace_with_placeholder, text_demojized)
    return protected, tokens


def restore_entities(text: str, tokens: List[str]) -> str:
    """Restaure les entités protégées par protect_entities() et réémojize.

    Tolère un espace inséré par le tokenizer entre "PLACEHOLDER" et l'index (ex.
    "PLACEHOLDER 0" au lieu de "PLACEHOLDER0") — observé le 2026-07-24 sur un texte tout-caps
    combiné à plusieurs placeholders (cf. README) : un remplacement par chaîne exacte ratait
    ces occurrences, laissant le placeholder brut dans le texte final.
    """
    for i, token in enumerate(tokens):
        text = re.sub(rf"PLACEHOLDER\s*{i}\b", token, text)
    return emoji.emojize(text, language="alias")


def _has_letters(text: Optional[str]) -> bool:
    return any(ch.isalpha() for ch in (text or "").strip())


_PLACEHOLDER_RE = re.compile(r"PLACEHOLDER\d+")


def _unwrap_package_translation(translation):
    """CachedTranslation (paire directe) expose `.underlying` -> PackageTranslation ;
    CompositeTranslation (pivot) n'a pas cet attribut, on la retourne telle quelle."""
    return getattr(translation, "underlying", translation)


def _split_into_sentences(hop_translation, text: str) -> List[str]:
    """
    Découpe `text` en phrases via le même sentencizer que celui utilisé en interne par
    `argostranslate.translate.apply_packaged_translation()`.

    Root cause confirmée le 2026-07-24 (Test 5, docs 61884107/61883117) : batcher un document
    entier comme une seule séquence (sans ce découpage) fait parfois s'arrêter le beam search
    après la première phrase, silencieusement — pas une troncature par `max_decoding_length`
    (14 tokens de sortie sur un texte à 56 tokens en entrée, testé), le modèle "décide" juste
    que la traduction est terminée après la 1ère phrase quand plusieurs phrases hétérogènes
    (changement de registre, emoji, texte cassé) sont concaténées sans démarcation claire.
    Traduire chaque phrase séparément (comme le fait l'API haut niveau d'Argos, qu'on
    contournait entièrement pour batcher) élimine le problème — validé sur les deux documents
    cassés : les 4/4 et 2/2 phrases sont alors traduites intégralement.
    """
    pkg_translation = _unwrap_package_translation(hop_translation)
    if pkg_translation.translator is None:
        pkg_translation.hypotheses("", num_hypotheses=1)  # force le chargement paresseux
    return pkg_translation.sentencizer.split_sentences(text) if text else []


def _batch_translate_sentences(hop_translation, sentences: List[str], num_hypotheses: int = 3):
    """
    Traduit une liste de PHRASES (pas de documents entiers) en un seul appel CTranslate2
    `translate_batch` — validé le 2026-07-23 sans impact sur la qualité (résultats bit-identiques
    un-par-un vs batché sur 60 textes réels, CTranslate2 traite chaque séquence indépendamment).

    `length_penalty=1.0` (défaut de CTranslate2) au lieu de `0.2` (défaut arbitraire d'Argos en
    interne) : validé sans régression sur 15 documents réels, corrige des troncatures
    multi-phrases et le bug tout-caps.

    Retourne `(pkg, hypotheses_par_phrase)` où `hypotheses_par_phrase[i]` est la liste des
    listes de TOKENS (pas décodées) pour la phrase `i`, classées par score décroissant — laissé
    non décodé pour permettre de concaténer les tokens de plusieurs phrases d'un même document
    avant un unique `decode()` final (même logique que `apply_packaged_translation()` en
    interne : décoder phrase par phrase puis `join()` les chaînes casse l'espacement/la
    ponctuation gérés par le tokenizer SentencePiece entre phrases).
    """
    import argostranslate.settings as argos_settings

    pkg_translation = _unwrap_package_translation(hop_translation)
    if pkg_translation.translator is None:
        pkg_translation.hypotheses("", num_hypotheses=1)
    pkg = pkg_translation.pkg
    translator = pkg_translation.translator

    tokenized = [pkg.tokenizer.encode(s) for s in sentences]
    target_prefix = [[pkg.target_prefix]] * len(tokenized) if pkg.target_prefix != "" else None
    results = translator.translate_batch(
        tokenized,
        target_prefix=target_prefix,
        replace_unknowns=True,
        max_batch_size=argos_settings.batch_size,
        batch_type="tokens",
        beam_size=max(num_hypotheses, argos_settings.beam_size),
        num_hypotheses=num_hypotheses,
        length_penalty=1.0,
        return_scores=True,
    )
    return pkg, [r.hypotheses for r in results]


def _pick_best_hypothesis_tokens(pkg, hypotheses: List[list]) -> Optional[list]:
    """Retourne la première liste de tokens non dégénérée (dont le texte décodé contient au
    moins une lettre) parmi des hypothèses classées par score décroissant. None si toutes sont
    dégénérées (`_has_letters` : protection contre le bug tout-caps, cf. README)."""
    for tokens in hypotheses:
        if _has_letters(pkg.tokenizer.decode(tokens)):
            return tokens
    return None


def _translate_documents_one_hop(hop_translation, texts: List[str]) -> List[Optional[str]]:
    """
    Traduit une liste de documents complets sur UN SEUL saut (pas de pivot ici), en découpant
    chaque document en phrases, en batchant TOUTES les phrases de TOUS les documents en un seul
    appel CTranslate2, puis en réassemblant chaque document par concaténation des tokens de ses
    phrases avant un decode() unique (cf. `_batch_translate_sentences`).

    La protection tout-caps+placeholders (cf. README, bug découvert le 2026-07-24) est appliquée
    par PHRASE, pas par document entier — plus précis (un document peut mélanger une phrase
    tout-caps et des phrases en casse normale) et strictement plus étroit que l'ancienne règle
    générale sur tout texte tout-caps (risque sur les noms propres/sigles limité à ce cas).

    Retourne `None` pour un document si toutes ses phrases sont dégénérées, ou dont le résultat
    ne conserve aucune phrase traduisible (traité comme un échec par l'appelant).
    """
    per_doc_sentences = [_split_into_sentences(hop_translation, t) for t in texts]
    flat_sentences = [s for sents in per_doc_sentences for s in sents]

    if not flat_sentences:
        return [None] * len(texts)

    needs_case_fix = [
        s.isupper() and "PLACEHOLDER" in s and _has_letters(_PLACEHOLDER_RE.sub("", s))
        for s in flat_sentences
    ]
    working_sentences = [s.lower() if fix else s for s, fix in zip(flat_sentences, needs_case_fix)]

    pkg, hyps_per_sentence = _batch_translate_sentences(hop_translation, working_sentences)
    best_tokens_per_sentence = [_pick_best_hypothesis_tokens(pkg, h) for h in hyps_per_sentence]

    # Restaure la casse par phrase (sur le texte décodé, avant de re-tokeniser pour la
    # concaténation finale — plus simple et robuste que manipuler des tokens en majuscules).
    decoded_per_sentence: List[Optional[str]] = []
    for tokens, fix in zip(best_tokens_per_sentence, needs_case_fix):
        if tokens is None:
            decoded_per_sentence.append(None)
            continue
        decoded = pkg.tokenizer.decode(tokens)
        decoded_per_sentence.append(decoded.upper() if fix else decoded)

    results: List[Optional[str]] = []
    idx = 0
    for sents in per_doc_sentences:
        n = len(sents)
        doc_sentences = [d for d in decoded_per_sentence[idx : idx + n] if d is not None]
        idx += n
        results.append(" ".join(doc_sentences) if doc_sentences else None)
    return results


def batch_translate(translation, texts: List[str]) -> List[Optional[str]]:
    """
    Traduit une liste de documents en un minimum d'appels CTranslate2, en gérant le pivot via
    `CompositeTranslation` en deux passes complètes (comme `CompositeTranslation.hypotheses()`
    le fait en interne : le texte anglais intermédiaire est entièrement réassemblé après le
    1er saut, puis re-découpé en phrases anglaises pour le 2e saut — pas de découpage porté à
    travers le pivot).

    `None` pour un document si toutes ses phrases sont dégénérées (traité comme un échec par
    l'appelant : document non mis à jour plutôt qu'écrasé).
    """
    import argostranslate.translate as argostranslate_translate

    if not texts:
        return []

    if isinstance(translation, argostranslate_translate.CompositeTranslation):
        intermediate = _translate_documents_one_hop(translation.t1, texts)
        intermediate = [t or "" for t in intermediate]
        return _translate_documents_one_hop(translation.t2, intermediate)
    return _translate_documents_one_hop(translation, texts)


def remove_extra_spaces(text: str) -> str:
    """Supprime les espaces superflus dans un texte."""
    cleaned_text = re.sub(r"\s+", " ", text)
    return cleaned_text.strip()


# --- Détection de langue (étape 1, factorisée pour être appelable indépendamment par
# utils/batch_coordinator.py sur plusieurs lots de documents avant de les fusionner) ---
@dataclass
class DetectionResult:
    """Sortie de `_detect_languages_for()` : tout ce qu'il faut pour grouper/traduire, sans
    dépendre des `Document` d'origine (une fois cette étape passée, seuls texts/doc_ids/
    detected_langs/doc_tokens comptent — plusieurs DetectionResult de requêtes différentes se
    fusionnent par simple concaténation de ces listes)."""

    texts: List[str]
    doc_ids: List[int]
    detected_langs: Dict[int, str]
    doc_tokens: Dict[int, List[str]]
    n_skipped_detection_failed: int
    n_fallback_en_unsupported_lang: int


def _detect_languages_for(
    documents: List[Document], target_lang: str, source_lang: str, force_translate: bool
) -> DetectionResult:
    """
    Détecte la langue source de chaque document d'un lot (étape 1 de `process_translation`,
    inchangée dans sa logique — juste extraite pour être appelable séparément sur plusieurs lots
    avant de les fusionner en un seul cycle de traduction, cf. utils/batch_coordinator.py).
    """
    detected_langs: Dict[int, str] = {}
    texts: List[str] = []
    doc_ids: List[int] = []
    doc_tokens: Dict[int, List[str]] = {}

    n_skipped_detection_failed = 0
    n_fallback_en_unsupported_lang = 0

    for doc in documents:
        if not force_translate:
            if target_lang == "fr" and doc.text_fr is not None:
                continue
            if target_lang == "en" and doc.text_en is not None:
                continue
        if not doc.text:
            continue

        plain = remove_html_tags(doc.text)

        if source_lang != "auto":
            lang = source_lang
        else:
            try:
                lang = detect_language(plain)
            except Exception as e:
                # Échec de détection (pas juste "langue non supportée") : on ne sait pas du
                # tout de quelle langue il s'agit, on laisse le document non traduit plutôt
                # que de deviner à l'aveugle.
                logging.info(
                    f"⚠️ Échec de détection pour le doc_id {doc.id}: {e}. Document ignoré."
                )
                n_skipped_detection_failed += 1
                continue
            if lang not in ALLOWED_LANGUAGES:
                # Langue détectée avec confiance, mais Argos Translate n'a aucun paquet pour
                # elle (ALLOWED_LANGUAGES couvre déjà tout ce qu'Argos sait traduire, cf.
                # README) : on traduit quand même via le pivot anglais plutôt que de laisser
                # le document non traduit — imparfait mais mieux que rien pour ces langues
                # qu'Argos ne supportera probablement jamais.
                logging.info(
                    f"⚠️ Langue détectée '{lang}' pour le doc_id {doc.id} non supportée par "
                    f"Argos Translate. Fallback sur 'en'."
                )
                n_fallback_en_unsupported_lang += 1
                lang = "en"
        detected_langs[doc.id] = lang

        protected_text, tokens = protect_entities(remove_extra_spaces(plain))
        doc_tokens[doc.id] = tokens
        texts.append(protected_text)
        doc_ids.append(doc.id)

    return DetectionResult(
        texts=texts,
        doc_ids=doc_ids,
        detected_langs=detected_langs,
        doc_tokens=doc_tokens,
        n_skipped_detection_failed=n_skipped_detection_failed,
        n_fallback_en_unsupported_lang=n_fallback_en_unsupported_lang,
    )


# --- Traduction (étape 2) sur un pool déjà détecté — potentiellement fusionné depuis plusieurs
# DetectionResult de requêtes différentes par utils/batch_coordinator.py. Logique de groupement/
# prefetch/traduction par langue INCHANGÉE par rapport à l'ancien process_translation() monolithique
# — seule l'entrée a changé (pool déjà détecté au lieu de documents bruts). ---
def _translate_detected(
    texts: List[str],
    doc_ids: List[int],
    detected_langs: Dict[int, str],
    doc_tokens: Dict[int, List[str]],
    target_lang: str,
    timer: PerfTimer,
) -> Tuple[Dict[int, Optional[str]], Dict[str, int]]:
    translations: Dict[int, Optional[str]] = {}
    stats = {"n_language_pairs": 0, "n_cache_hits": 0, "n_cache_misses": 0}

    if not texts:
        return translations, stats

    # Regroupement des textes par langue détectée
    groups: Dict[str, Dict[str, List]] = {}
    for idx, doc_id in enumerate(doc_ids):
        lang = detected_langs[doc_id]
        if lang not in groups:
            groups[lang] = {"ids": [], "texts": []}
        groups[lang]["ids"].append(doc_id)
        groups[lang]["texts"].append(texts[idx])

    # Préchargement en parallèle de tous les paquets Argos nécessaires pour ce lot (une seule
    # attente réseau au lieu d'une par langue traitée séquentiellement, cf. prefetch_packages
    # dans utils/cache.py) — la boucle de traduction ci-dessous reste inchangée dans sa logique,
    # elle retombera simplement sur un cache déjà chaud dans le cas courant. `prefetch_cache_hits`
    # remplace `was_cache_hit` de `get_or_load_translation()` pour les stats : après préchargement,
    # ce dernier verrait toujours un hit, même pour un paquet tout juste téléchargé.
    source_langs = [lang for lang in groups if lang != target_lang]
    with timer.model_load():
        prefetch_cache_hits = prefetch_packages(target_lang, source_langs) if source_langs else {}

    # Traduction par groupe de langue détectée
    for lang, group in groups.items():
        if lang == target_lang:
            translated_group: List[Optional[str]] = list(group["texts"])
        else:
            # Logs de début/fin de lot (2026-08-06) : jusqu'ici seuls les téléchargements de
            # paquets Argos (utils/cache.py) et les erreurs étaient visibles en direct dans les
            # logs -- aucun signal pour savoir qu'un lot de traduction est EN COURS (potentiellement
            # plusieurs secondes/minutes, chargement de modèle inclus) ni combien de temps il a pris,
            # avant l'entrée [PERF] agrégée en fin de cycle harvester complet.
            n_docs_in_group = len(group["texts"])
            logging.info(
                f"🔄 Traduction du lot démarrée | {lang}→{target_lang} | documents={n_docs_in_group}"
            )
            batch_started_at = time.monotonic()
            try:
                with timer.model_load():
                    translation, _ = get_or_load_translation(lang, target_lang)
                stats["n_language_pairs"] += 1
                was_cache_hit = prefetch_cache_hits.get(lang, False)
                stats["n_cache_hits" if was_cache_hit else "n_cache_misses"] += 1

                with timer.inference():
                    translated_group = batch_translate(translation, group["texts"])
                logging.info(
                    f"✅ Lot traduit | {lang}→{target_lang} | documents={n_docs_in_group} | "
                    f"{(time.monotonic() - batch_started_at) * 1000:.0f}ms"
                )
            except Exception as e:
                # Filet de sécurité (2026-07-26) : un échec de vérification MD5 par Stanza sur
                # un paquet déjà installé (root cause pas formellement confirmée, cf.
                # evict_translation_packages) est un état corrompu, pas une langue non
                # supportée — un réinstall propre + une seule retentative suffit généralement.
                # Ne PAS retenter pour les autres erreurs (ex. langue vraiment non supportée) :
                # ça ne ferait que répéter un échec garanti pour rien.
                if "md5" in str(e).lower():
                    logging.warning(
                        f"⚠️ Échec MD5 Stanza pour '{lang}', paquet probablement corrompu — "
                        f"réinstall + 1 nouvelle tentative | {e}"
                    )
                    evict_translation_packages(lang, target_lang)
                    try:
                        with timer.model_load():
                            translation, _ = get_or_load_translation(lang, target_lang)
                        stats["n_language_pairs"] += 1
                        stats["n_cache_misses"] += 1
                        with timer.inference():
                            translated_group = batch_translate(translation, group["texts"])
                        logging.info(
                            f"✅ Lot traduit (après réinstall) | {lang}→{target_lang} | "
                            f"documents={n_docs_in_group} | "
                            f"{(time.monotonic() - batch_started_at) * 1000:.0f}ms"
                        )
                        for doc_id, txt in zip(group["ids"], translated_group):
                            translations[doc_id] = (
                                restore_entities(txt, doc_tokens.get(doc_id, [])) if txt is not None else None
                            )
                        continue
                    except Exception as e2:
                        # Toujours pas critique : le paquet corrompu a été évincé du disque,
                        # le prochain run repartira d'un état propre. Les docs concernés
                        # restent non traduits pour ce passage, pas perdus.
                        logging.warning(f"⚠️ Échec persistant après réinstall pour '{lang}': {e2}")
                        translated_group = [None] * len(group["texts"])
                else:
                    logging.error(f"🚨 Erreur lors de la traduction pour la langue '{lang}': {e}")
                    translated_group = [None] * len(group["texts"])

        for doc_id, txt in zip(group["ids"], translated_group):
            translations[doc_id] = (
                restore_entities(txt, doc_tokens.get(doc_id, [])) if txt is not None else None
            )

    return translations, stats


def _write_translations(
    db, target_lang: str, translations: Dict[int, Optional[str]], detected_langs: Dict[int, str],
    timer: PerfTimer,
) -> int:
    """Écrit en DB les traductions d'un lot (doc_id -> texte traduit). Factorisée (2026-07-27) pour
    être appelable aussi bien directement (mode solo) que par le coordinateur de fusion
    (utils/batch_coordinator.py, injectée comme write_fn — le "harvester" peut l'appeler plusieurs
    fois de suite s'il draine le pool en plusieurs vagues).

    N'écrase jamais Document.text_fr/text_en avec NULL pour un doc en échec de traduction (exclu
    de l'update, reste non traduit jusqu'au prochain run plutôt que de perdre une traduction déjà
    valide en base). Retourne le nombre de documents effectivement mis à jour."""
    update_ids = [doc_id for doc_id, txt in translations.items() if txt is not None]
    if not update_ids:
        return 0

    if target_lang == "fr":
        stmt = (
            update(Document)
            .where(Document.id.in_(update_ids))
            .values(
                text_fr=case(
                    *[(Document.id == doc_id, translations[doc_id]) for doc_id in update_ids],
                    else_=None,
                ),
                lang=case(
                    *[(Document.id == doc_id, detected_langs.get(doc_id, None)) for doc_id in update_ids],
                    else_=None,
                ),
                last_updated_at=func.now(),
            )
        )
    elif target_lang == "en":
        stmt = (
            update(Document)
            .where(Document.id.in_(update_ids))
            .values(
                text_en=case(
                    *[(Document.id == doc_id, translations[doc_id]) for doc_id in update_ids],
                    else_=None,
                ),
                lang=case(
                    *[(Document.id == doc_id, detected_langs.get(doc_id, None)) for doc_id in update_ids],
                    else_=None,
                ),
                last_updated_at=func.now(),
            )
        )
    else:
        return 0

    with timer.sql_write(n_items=len(update_ids)):
        db.execute(stmt)
        db.commit()
    return len(update_ids)


def serialize_dates(obj):
    """Remplace récursivement datetime/date par leur isoformat pour JSON."""
    if isinstance(obj, dict):
        return {k: serialize_dates(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_dates(v) for v in obj]
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    return obj


# --- Validation des paramètres de la requête ---
class RequestParams(BaseModel):
    """Schéma de validation des paramètres de la requête HTTP.

    Identique au contrat de gcp-fct-translation (remplacement direct) : mêmes paramètres,
    mêmes tables lues/écrites — seul le backend de traduction change (Argos Translate en
    local avec cache LRU par paquet, au lieu d'un appel HTTP vers le service Cloud Run
    LibreTranslate).
    """

    lang: Optional[str] = Field(None, description="Langue cible ('fr' ou 'en').")
    source_lang: Optional[str] = Field(
        "auto", description="Langue source, sinon 'auto'."
    )
    importation_ids: Optional[list[int]] = Field(
        None, description="Liste d'IDs d'importations. Accepte aussi un int ou une string CSV ('22,696')."
    )
    just_translate_ids: Optional[List[int]] = Field(
        None, description="Liste d'IDs de documents à traduire."
    )
    force_translate: Optional[bool] = Field(
        False, description="Forcer l'écrasement des traductions existantes."
    )
    task_queue: Optional[str] = Field(
        "libretranslate-v2", description="Nom de la file d'attente."
    )
    batch_size: Optional[int] = Field(
        100,
        description=(
            "Nombre de documents par tâche Cloud Tasks une fois le découpage décidé (cf. "
            "dispatch_threshold) — ne fixe que la taille du lot, pas la décision de découper. "
            "Volontairement petit : le coordinateur de fusion regroupe déjà les lots au niveau du "
            "pool partagé (utils/batch_coordinator.py), un batch_size élevé n'apporte plus rien "
            "côté GPU et ralentit juste chaque tâche (cf. README)."
        ),
    )
    dispatch_threshold: Optional[int] = Field(
        200,
        description=(
            "Nombre de documents au-delà duquel on découpe en plusieurs tâches Cloud Tasks "
            "plutôt que de tout traiter dans l'invocation en cours. Distinct de batch_size (taille "
            "de chaque lot une fois découpé). Volontairement bas : `max_instance_count=1` sur "
            "cette CF rend obsolète l'ancienne justification d'un seuil élevé (cf. README)."
        ),
    )
    start_date: Optional[datetime.datetime] = Field(
        None, description="Date de début (YYYY-MM-DD) pour filtrer les posts."
    )
    end_date: Optional[datetime.datetime] = Field(
        None, description="Date de fin (YYYY-MM-DD) pour filtrer les posts."
    )
    parent_job_id: Optional[str] = Field(None, description="Identifiant du job parent (pour les tâches découpées)")
    document_type: Optional[str] = Field(
        "text", description="Type de document à traiter : 'text', 'image' ou 'video'...."
    )
    post_lang_detection: Optional[bool] = Field(
        False, description="Si True, détecte la langue de documents.text et met à jour posts.lang."
    )
    max_retries: Optional[int] = Field(5, description="Nombre maximum de tentatives avant traitement un-par-un.")
    retry_count: Optional[int] = Field(0, description="Compteur interne de tentatives (géré automatiquement).")
    translate_chunks: Optional[bool] = Field(False, description="Si True, traduit les chunks de transcripts de l'analyse. Nécessite analyse_id.")
    analyse_id: Optional[int] = Field(None, description="ID de l'analyse dont les chunks sont à traduire (utilisé avec translate_chunks=True).")
    call_back_pipeline: Optional[dict] = Field(
        None,
        description=(
            "Callback vers le pipeline appelant (mêmes clés que gcp-fct-translation : "
            "queue_name/task_name/url/payload) — déclenché dès que TOUS les documents de ce "
            "parent_job_id sont écrits, y compris fusionnés dans un harvester d'un autre job "
            "(cf. README, coordinateur de fusion)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_importation_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "importation_id" in data and "importation_ids" not in data:
                legacy = data.pop("importation_id")
                if legacy is not None:
                    data["importation_ids"] = legacy
        return data

    @field_validator("importation_ids", mode="before")
    @classmethod
    def normalize_importation_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            return [int(v) for v in value if v is not None]
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            if "," in cleaned:
                return [int(x.strip()) for x in cleaned.split(",") if x.strip()]
            return [int(cleaned)]
        return value

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_date(cls, v):
        if isinstance(v, str):
            v = datetime.datetime.fromisoformat(v)
        if isinstance(v, datetime.datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=datetime.timezone.utc)
        return v

    @model_validator(mode="after")
    def _clamp_batch_size_to_dispatch_threshold(self) -> "RequestParams":
        # dispatch_threshold décide QUAND découper, batch_size la taille de chaque lot une fois
        # découpé : si batch_size > dispatch_threshold, un sous-lot dépasserait encore le seuil
        # sur sa propre invocation et se re-découperait à l'identique indéfiniment (aucune
        # convergence, cf. les 3 branches de découpage dans run()). Filet de sécurité plutôt
        # qu'une erreur 400 : un batch_size mal configuré ne doit pas bloquer un appelant existant.
        if self.batch_size > self.dispatch_threshold:
            logging.warning(
                f"⚠️ batch_size ({self.batch_size}) > dispatch_threshold ({self.dispatch_threshold}) "
                f"— ramené à dispatch_threshold pour éviter un re-découpage sans fin."
            )
            self.batch_size = self.dispatch_threshold
        return self

    @model_validator(mode="after")
    def _require_scope(self) -> "RequestParams":
        if not self.translate_chunks and not self.importation_ids and not self.just_translate_ids:
            raise ValueError(
                "importation_ids ou just_translate_ids requis (sauf translate_chunks=True) — "
                "une requête sans aucune des deux scannerait toute la base et provoque un OOM."
            )
        return self


# --- Détection de langue et mise à jour de posts.lang (identique à gcp-fct-translation) ---
def detect_post_languages(
    db, query_filters: dict, document_type: str = "text"
) -> Tuple[int, int]:
    """
    Pour chaque post, détecte la langue à partir du document associé
    et met à jour posts.lang. Retourne (nb_updated, nb_total).
    """
    query = (
        db.query(Post, Document)
        .join(
            PostDocumentAssociation,
            Post.id == PostDocumentAssociation.from_post_id,
        )
        .join(
            Document,
            Document.id == PostDocumentAssociation.to_document_id,
        )
        .filter(Document.type == document_type)
    )
    if query_filters.get("importation_ids"):
        query = query.filter(Post.importation_id.in_(query_filters["importation_ids"]))
    if query_filters.get("just_translate_ids"):
        query = query.filter(Document.id.in_(query_filters["just_translate_ids"]))
    if query_filters.get("start_date"):
        query = query.filter(Post.created_at >= query_filters["start_date"])
    if query_filters.get("end_date"):
        query = query.filter(Post.created_at <= query_filters["end_date"])

    rows = query.all()
    if not rows:
        return 0, 0

    post_langs: Dict[int, str] = {}
    for post, doc in rows:
        if not doc.text or post.id in post_langs:
            continue
        plain = remove_html_tags(doc.text)
        try:
            lang = detect_language(plain)
        except Exception as e:
            logging.debug(f"  post_id={post.id} → échec détection ({e})")
            lang = None
        if lang is not None:
            post_langs[post.id] = lang

    lang_counts = {}
    for lang in post_langs.values():
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    logging.info(f"🌐 Langues détectées : {lang_counts} ({len(post_langs)}/{len(rows)} posts)")

    if not post_langs:
        return 0, len(rows)

    stmt = (
        update(Post)
        .where(Post.id.in_(list(post_langs.keys())))
        .values(
            lang=case(
                *[(Post.id == post_id, lang) for post_id, lang in post_langs.items()],
                else_=None,
            )
        )
    )
    db.execute(stmt)
    db.commit()
    return len(post_langs), len(rows)


def process_chunk_translation(
    chunks: List[Chunk], target_lang: str, source_lang: str, timer: PerfTimer
) -> Tuple[Dict[int, Optional[str]], Dict[str, int]]:
    """Traduit les chunks de transcripts WhisperX via Argos Translate. Texte déjà propre,
    pas de nettoyage HTML — mais toujours protégé (mentions/hashtags/urls/emojis) avant
    traduction, comme pour les documents (cf. protect_entities)."""
    texts = []
    chunk_ids = []
    chunk_tokens: Dict[int, List[str]] = {}
    for chunk in chunks:
        if not chunk.text:
            continue
        protected_text, tokens = protect_entities(chunk.text)
        chunk_tokens[chunk.id] = tokens
        texts.append(protected_text)
        chunk_ids.append(chunk.id)

    stats = {"n_language_pairs": 0, "n_cache_hits": 0, "n_cache_misses": 0}

    if not texts:
        return {}, stats

    actual_source = source_lang
    if source_lang == "auto":
        try:
            detected = detect_language(texts[0])
            actual_source = detected if detected in ALLOWED_LANGUAGES else "auto"
        except Exception:
            actual_source = "auto"

    if actual_source == target_lang or actual_source == "auto":
        return {
            cid: restore_entities(t, chunk_tokens.get(cid, []))
            for cid, t in zip(chunk_ids, texts)
        }, stats

    with timer.model_load():
        translation, was_cache_hit = get_or_load_translation(actual_source, target_lang)
    stats["n_language_pairs"] += 1
    stats["n_cache_hits" if was_cache_hit else "n_cache_misses"] += 1

    with timer.inference():
        translated_texts = batch_translate(translation, texts)
        result = {
            cid: (restore_entities(translated, chunk_tokens.get(cid, [])) if translated is not None else None)
            for cid, translated in zip(chunk_ids, translated_texts)
        }
    return result, stats


# --- Cloud Function ---
@log_and_return_response
def run(request: Request):
    try:
        params = RequestParams(
            **(request.json if request.method == "POST" else request.args)
        )
        if not params.post_lang_detection and not params.lang:
            return "Paramètre 'lang' requis en mode traduction.", 400
        if (
            params.start_date
            and params.end_date
            and params.start_date > params.end_date
        ):
            return "La date de début ne peut pas être supérieure à la date de fin.", 400
        logging.info(f"🚀 Nouvelle requête reçue. Paramètres : {params}")
    except ValidationError as e:
        return f"Paramètres invalides : {e}", 400

    if params.parent_job_id is None:
        dumped_params = params.model_dump(exclude_unset=True)
        job_id = make_task_id(dumped_params)
    else:
        job_id = params.parent_job_id
    prefix = "libretranslatev2"
    logging.info(f"Démarrage du job_id : {job_id}")

    if not request.headers.get("X-CloudTasks-TaskName"):
        logging.info(f"🚀 [FIRE-AND-FORGET] Appel initial détecté. Dispatching job {job_id} vers Cloud Tasks...")
        cloud_task_client = CloudTasks()
        task_payload = params.model_dump(exclude_unset=True)
        task_payload["parent_job_id"] = job_id
        task_payload = serialize_dates(task_payload)
        task_name = f"{prefix}-j{job_id}-init"
        task = CloudTask(
            queue_name=params.task_queue,
            name=task_name,
            http_method="POST",
            url=SERVICE_URL,
            payload=task_payload,
            include_oidc_token=True,
            duplicate_prevention_window_minutes=1,
            timeout=540,
        )
        cloud_task_client.add_task(task)
        logging.info(f"✅ Tâche Cloud Tasks créée : {task_name}")
        return {
            "status": "accepted",
            "job_id": job_id,
            "message": "Job dispatché vers Cloud Tasks. Utilisez job_id pour suivre la progression."
        }, 202

    retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", 0))
    timer = PerfTimer(
        function_name="libretranslate-v2",
        parent_job_id=params.parent_job_id,
        retry_count=retry_count,
    )

    with timer.db_connect():
        db_gen = get_db()
        db = next(db_gen)
        # Filet de sécurité (2026-07-26, cf. README) : des lectures de plusieurs MINUTES pour
        # quelques dizaines de lignes ont été observées en prod (probable contention DB pendant
        # les pics de charge, root cause pas formellement confirmée) — sans borne, une requête
        # bloquée immobilise une instance GPU entière pour rien. 120s > la plus grosse lecture
        # légitime observée (~19s pour 25 349 lignes) mais très en dessous des 234-335s constatés
        # en pratique. `SET LOCAL` ne s'applique qu'à la transaction en cours (même pattern déjà
        # établi dans gcp-fct-coordination).
        db.execute(text("SET LOCAL statement_timeout = '120s'"))
    try:
        ## Mode détection de langue uniquement
        if params.post_lang_detection:
            lang_query = (
                db.query(Document.id)
                .join(
                    PostDocumentAssociation,
                    Document.id == PostDocumentAssociation.to_document_id,
                )
                .join(Post, Post.id == PostDocumentAssociation.from_post_id)
                .filter(Document.type == params.document_type)
            )
            if params.importation_ids:
                lang_query = lang_query.filter(Post.importation_id.in_(params.importation_ids))
            if params.just_translate_ids:
                lang_query = lang_query.filter(Document.id.in_(params.just_translate_ids))
            if params.start_date:
                lang_query = lang_query.filter(Post.created_at >= params.start_date)
            if params.end_date:
                lang_query = lang_query.filter(Post.created_at <= params.end_date)
            if not params.force_translate:
                lang_query = lang_query.filter(Post.lang.is_(None))
            # .distinct() (2026-08-07, même bug de fan-out que le mode traduction par défaut plus
            # bas -- un document référencé par plusieurs posts de l'importation apparaîtrait sinon
            # plusieurs fois dans doc_ids, une LISTE qui ne déduplique pas d'elle-même).
            doc_ids = [row[0] for row in lang_query.distinct().all()]
            logging.info(f"🔍 Détection de langue : {len(doc_ids)} documents trouvés.")

            if not doc_ids:
                return {"message": "0️⃣ Aucun document à traiter pour la détection de langue", "job_id": job_id}, 200

            if len(doc_ids) > params.dispatch_threshold:
                batches = [
                    doc_ids[i : i + params.batch_size]
                    for i in range(0, len(doc_ids), params.batch_size)
                ]
                logging.info(f"📦 Découpage en {len(batches)} lots de {params.batch_size} documents.")
                cloud_task_client = CloudTasks()
                common_params = json.loads(params.model_dump_json(exclude_unset=True))
                common_params.pop("just_translate_ids", None)
                task_ids = []
                for batch in batches:
                    task_payload = {**common_params, "just_translate_ids": batch, "parent_job_id": job_id}
                    task_id = make_task_id(task_payload)
                    task_ids.append(task_id)
                    task = CloudTask(
                        queue_name=params.task_queue,
                        name=f"{prefix}-j{job_id}-langdetect-t{task_id}",
                        http_method="POST",
                        url=SERVICE_URL,
                        payload=task_payload,
                        include_oidc_token=True,
                        timeout=TASK_TIMEOUT_SECONDS,
                    )
                    cloud_task_client.add_task(task)
                logging.info(f"✅ {len(task_ids)} tâches Cloud Tasks créées (job_id={job_id}).")
                return {
                    "message": f"Création de {len(task_ids)} tâches de détection de langue pour {len(doc_ids)} documents.",
                    "job_id": job_id,
                }, 200

            logging.info(f"⚙️ Traitement synchrone de {len(doc_ids)} documents.")
            query_filters = {
                "importation_ids": params.importation_ids,
                "just_translate_ids": doc_ids,
                "start_date": params.start_date,
                "end_date": params.end_date,
            }
            nb_updated, nb_total = detect_post_languages(db, query_filters, document_type=params.document_type)
            logging.info(f"✅ Détection terminée : {nb_updated}/{nb_total} posts mis à jour.")
            return {"message": f"✅ {nb_updated}/{nb_total} posts mis à jour avec leur langue détectée", "job_id": job_id}, 200

        ## Mode traduction des chunks de transcripts
        if params.translate_chunks:
            if not params.analyse_id:
                return "Paramètre 'analyse_id' requis avec translate_chunks=True.", 400

            if params.just_translate_ids:
                chunks_to_translate = db.query(Chunk).filter(
                    Chunk.analyse_id == params.analyse_id,
                    Chunk.id.in_(params.just_translate_ids),
                    Chunk.text.isnot(None),
                ).all()
            else:
                all_chunks = db.query(Chunk).filter(
                    Chunk.analyse_id == params.analyse_id,
                    Chunk.text.isnot(None),
                ).all()

                if not all_chunks:
                    return {"message": "0️⃣ Aucun chunk à traduire", "job_id": job_id}, 200

                if len(all_chunks) > params.dispatch_threshold:
                    chunk_ids_all = [c.id for c in all_chunks]
                    batches = [
                        chunk_ids_all[i : i + params.batch_size]
                        for i in range(0, len(chunk_ids_all), params.batch_size)
                    ]
                    cloud_task_client = CloudTasks()
                    common_params = {
                        "analyse_id": params.analyse_id,
                        "lang": params.lang,
                        "translate_chunks": True,
                        "source_lang": params.source_lang,
                        "parent_job_id": job_id,
                        "task_queue": params.task_queue,
                        "batch_size": params.batch_size,
                    }
                    task_ids = []
                    for batch in batches:
                        task_payload = {**common_params, "just_translate_ids": batch}
                        task_id = make_task_id(task_payload)
                        task_ids.append(task_id)
                        task = CloudTask(
                            queue_name=params.task_queue,
                            name=f"{prefix}-j{job_id}-chunks-a{params.analyse_id}-t{task_id}",
                            http_method="POST",
                            url=SERVICE_URL,
                            payload=task_payload,
                            include_oidc_token=True,
                            timeout=TASK_TIMEOUT_SECONDS,
                        )
                        cloud_task_client.add_task(task)
                    return {
                        "message": f"Création de {len(task_ids)} tâches de traduction pour {len(all_chunks)} chunks.",
                        "job_id": job_id,
                    }, 200

                chunks_to_translate = all_chunks

            if not chunks_to_translate:
                return {"message": "0️⃣ Aucun chunk à traduire", "job_id": job_id}, 200

            translations, chunk_stats = process_chunk_translation(
                chunks_to_translate, params.lang, params.source_lang or "auto", timer
            )

            update_ids = [cid for cid, txt in translations.items() if txt is not None]
            if update_ids:
                stmt = (
                    update(Chunk)
                    .where(Chunk.id.in_(update_ids))
                    .values(
                        text=case(
                            *[(Chunk.id == cid, translations[cid]) for cid in update_ids],
                            else_=Chunk.text,
                        )
                    )
                )
                db.execute(stmt)
                db.commit()

            logging.info(f"✅ {len(update_ids)} chunks traduits (analyse_id={params.analyse_id})")
            timer.emit(chunk_stats)
            return {"message": f"✅ {len(update_ids)} chunks traduits", "job_id": job_id}, 200

        ## Mode par défaut : traduction des documents

        query = db.query(Document).filter(Document.type == params.document_type)
        if params.start_date or params.end_date:
            query = query.join(
                PostDocumentAssociation,
                Document.id == PostDocumentAssociation.to_document_id,
            ).join(Post, Post.id == PostDocumentAssociation.from_post_id)
            if params.start_date:
                query = query.filter(Post.created_at >= params.start_date)
            if params.end_date:
                query = query.filter(Post.created_at <= params.end_date)
        if params.importation_ids:
            query = query.filter(Document.importation_id.in_(params.importation_ids))
        if params.just_translate_ids:
            query = query.filter(Document.id.in_(params.just_translate_ids))
        if not params.force_translate:
            if params.lang == "fr":
                query = query.filter(Document.text_fr.is_(None))
            elif params.lang == "en":
                query = query.filter(Document.text_en.is_(None))
        # .distinct() (2026-08-07, bug réel constaté en prod sur libretranslate-v3, même requête) :
        # le join Post ci-dessus (filtre start_date/end_date) fait un fan-out sur Document si un
        # même document est référencé par PLUSIEURS posts de l'importation (média repartagé,
        # retweet/citation) -- sans .distinct(), ce document apparaît une fois par post associé dans
        # `documents`, gonflant artificiellement le total annoncé au dispatch (mesuré : 100 868
        # documents "trouvés" pour seulement 93 671 documents distincts réellement écrits --
        # confirmé un run réel complet, pas une perte, cf. translations: Dict[int, ...] qui
        # déduplique déjà naturellement en aval).
        with timer.sql_read():
            documents = query.distinct().all()
        timer.n_items_read = len(documents)

        if not documents:
            # Bug trouvé en test réel (2026-07-29) : ce retour précoce court-circuite entièrement
            # le coordinateur de fusion — sans ce filet, un job dont la lecture DB ne remonte
            # AUCUN document (ex. déjà tout traduit) ne recevait jamais son callback et restait
            # bloqué indéfiniment (aucun poll de secours, décision explicite). Même logique que
            # `outcome.own_n_detected == 0` plus bas (rien à drainer, rien à écrire).
            if params.call_back_pipeline:
                fire_callback_if_last(job_id, params.call_back_pipeline, params.task_queue)
            return {"message": "0️⃣ Aucun document à traduire", "job_id": job_id}, 200

        doc_ids = [doc.id for doc in documents]
        if len(documents) > params.dispatch_threshold:
            batches = [
                doc_ids[i : i + params.batch_size]
                for i in range(0, len(doc_ids), params.batch_size)
            ]
            cloud_task_client = CloudTasks()
            common_params = json.loads(params.model_dump_json(exclude_unset=True))
            common_params.pop("just_translate_ids", None)
            task_ids = []
            for batch in batches:
                task_payload = {**common_params, "just_translate_ids": batch, "parent_job_id": job_id}
                task_id = make_task_id(task_payload)
                task_ids.append(task_id)
                task = CloudTask(
                    queue_name=params.task_queue,
                    name=f"{prefix}-j{job_id}-t{task_id}",
                    http_method="POST",
                    url=SERVICE_URL,
                    payload=task_payload,
                    include_oidc_token=True,
                    timeout=TASK_TIMEOUT_SECONDS,
                )
                cloud_task_client.add_task(task)
            timer.n_tasks_dispatched = len(task_ids)
            timer.emit({"dispatch_type": "batch_split"})
            return {
                    "message": f"Création de {len(task_ids)} tâches de traduction pour {len(documents)} documents.",
                    "job_id": job_id,
                }, 200

        ## Traitement via le coordinateur de fusion (cf. utils/batch_coordinator.py) : cette
        ## requête détecte toujours ses propres documents elle-même, puis dépose (role="deposited",
        ## répond immédiatement) ou devient harvester (traduit+écrit tout ce qu'elle a pu drainer).
        def _dispatch_remaining(doc_ids: List[int], doc_job_ids: Dict[int, str], job_callbacks: Dict[str, dict]) -> None:
            # Appelée en dernier recours par le coordinateur (harvester jamais relevé, deadline
            # dure atteinte) : planifie des Cloud Tasks pour ce qui reste plutôt que de compter sur
            # une requête organique future (`_pools` est en mémoire du process). Regroupé par job
            # d'origine (2026-07-29) — un lot fusionné peut couvrir plusieurs pipelines, chacun
            # avec son propre `call_back_pipeline` : tout recoller sous le job courant leur ferait
            # perdre silencieusement leur callback.
            cloud_task_client = CloudTasks()
            common_params = json.loads(params.model_dump_json(exclude_unset=True))
            common_params.pop("just_translate_ids", None)
            common_params.pop("call_back_pipeline", None)
            dispatch_ts = int(time.time() * 1000)

            by_origin: Dict[Optional[str], List[int]] = {}
            for did in doc_ids:
                by_origin.setdefault(doc_job_ids.get(did), []).append(did)

            n_tasks = 0
            for origin_job_id, ids in by_origin.items():
                target_job_id = origin_job_id or job_id
                origin_callback = job_callbacks.get(origin_job_id) if origin_job_id else None
                batches = [ids[i:i + params.batch_size] for i in range(0, len(ids), params.batch_size)]
                for idx, batch in enumerate(batches):
                    task_payload = {**common_params, "just_translate_ids": batch, "parent_job_id": target_job_id}
                    if origin_callback:
                        task_payload["call_back_pipeline"] = origin_callback
                    task = CloudTask(
                        queue_name=params.task_queue,
                        name=f"{prefix}-j{target_job_id}-continuation-{dispatch_ts}-{idx}",
                        http_method="POST",
                        url=SERVICE_URL,
                        payload=task_payload,
                        include_oidc_token=True,
                        timeout=TASK_TIMEOUT_SECONDS,
                    )
                    cloud_task_client.add_task(task)
                    n_tasks += 1
            logging.info(
                f"✅ {n_tasks} tâche(s) de continuation créée(s) pour {len(doc_ids)} documents "
                f"restants, regroupées par job d'origine ({len(by_origin)} job(s), job_id={job_id})."
            )

        outcome = translate_via_coordinator(
            documents, params.lang, params.source_lang, params.force_translate, timer, db,
            detect_fn=_detect_languages_for, translate_fn=_translate_detected, write_fn=_write_translations,
            dispatch_remaining_fn=_dispatch_remaining,
            parent_job_id=job_id, call_back_pipeline=params.call_back_pipeline, queue_name=params.task_queue,
        )

        if params.call_back_pipeline and outcome.own_n_detected == 0 and outcome.role in ("deposited", "harvester"):
            # Ce job n'a détecté AUCUN document — il n'est jamais entré dans le pool/dans aucune
            # vague (rien à drainer, rien à écrire), donc le mécanisme normal de callback (après
            # écriture d'une vague, cf. batch_coordinator.py) ne se déclenchera jamais pour lui.
            fire_callback_if_last(job_id, params.call_back_pipeline, params.task_queue)

        if outcome.role == "deposited":
            # timer.emit() ici (même sans écriture) : sans ça, ces tâches n'apparaissent jamais
            # dans les logs [PERF] et wait_for_tasks() (notebooks perf) ne peut jamais les compter
            # comme "terminées" — n_items_written reste à 0 (valeur par défaut de PerfTimer, pas
            # None), donc bien compté comme "done" par les notebooks qui filtrent sur notna().
            timer.emit({"role": "deposited", "own_docs": len(documents)})
            return {
                "message": "🧩 Documents détectés et déposés — traduction prise en charge par un autre cycle en cours",
                "job_id": job_id,
            }, 200

        if outcome.role in ("solo_error", "harvester_error"):
            e = outcome.error
            failed_doc_ids = outcome.doc_ids or []
            # `doc_job_ids`/`job_callbacks_snapshot` ne sont peuplés que pour harvester_error (un
            # lot fusionné peut couvrir plusieurs jobs d'origine) — solo_error échoue AVANT tout
            # contact avec le pool, donc tous ses doc_ids appartiennent forcément à CE job.
            doc_job_ids = outcome.doc_job_ids or {}
            job_callbacks_snapshot = outcome.job_callbacks_snapshot or {}
            cloud_task_client = CloudTasks()
            common_params = json.loads(params.model_dump_json(exclude_unset=True))
            common_params.pop("just_translate_ids", None)
            common_params.pop("call_back_pipeline", None)

            if len(failed_doc_ids) <= 1:
                logging.error(
                    f"🚨 Échec persistant sur doc_id={failed_doc_ids[0] if failed_doc_ids else '?'} "
                    f"({e}). Document ignoré."
                )
                return {"message": "🚨 Échec persistant sur 1 document. Document ignoré.", "job_id": job_id}, 200

            split_in_half = params.retry_count < params.max_retries
            if split_in_half:
                logging.warning(
                    f"⚠️ Erreur transitoire ({e}) — tentative {params.retry_count + 1}/{params.max_retries}. "
                    f"Re-dispatch en 2 lots par job d'origine."
                )
            else:
                logging.warning(
                    f"⚠️ Max retries atteint ({params.max_retries}) — traitement un-par-un de "
                    f"{len(failed_doc_ids)} docs."
                )

            # Regroupe par job d'origine (2026-07-29) : un lot fusionné (harvester_error) peut
            # couvrir plusieurs pipelines — tout recoller sous le job COURANT leur ferait perdre
            # silencieusement leur callback. `None` = pas de job tracké (cron/bulk, ou solo_error).
            by_origin: Dict[Optional[str], List[int]] = {}
            for did in failed_doc_ids:
                by_origin.setdefault(doc_job_ids.get(did), []).append(did)

            n_batches = 0
            for origin_job_id, ids in by_origin.items():
                target_job_id = origin_job_id or job_id
                origin_callback = (
                    job_callbacks_snapshot.get(origin_job_id) if origin_job_id else params.call_back_pipeline
                )
                if split_in_half:
                    mid = len(ids) // 2
                    group_batches = [b for b in (ids[:mid], ids[mid:]) if b]
                else:
                    group_batches = [[doc_id] for doc_id in ids]

                for batch in group_batches:
                    task_payload = {
                        **common_params,
                        "just_translate_ids": batch,
                        "parent_job_id": target_job_id,
                        "retry_count": params.retry_count + 1,
                    }
                    if origin_callback:
                        task_payload["call_back_pipeline"] = origin_callback
                    task_id = make_task_id(task_payload)
                    task = CloudTask(
                        queue_name=params.task_queue,
                        name=f"{prefix}-j{target_job_id}-r{params.retry_count + 1}-t{task_id}",
                        http_method="POST",
                        url=SERVICE_URL,
                        payload=task_payload,
                        include_oidc_token=True,
                        timeout=TASK_TIMEOUT_SECONDS,
                    )
                    cloud_task_client.add_task(task)
                    n_batches += 1

            return {
                "message": (
                    f"⚠️ Erreur de traduction. Re-dispatch de {len(failed_doc_ids)} documents en "
                    f"{n_batches} lot(s) ({len(by_origin)} job(s) d'origine)."
                ),
                "job_id": job_id,
            }, 200

        # outcome.role == "harvester" : traduction+écriture faites (potentiellement pour plusieurs
        # requêtes d'origine fusionnées, cf. outcome.n_docs vs len(documents)).
        if outcome.n_written == 0:
            return {"message": "0️⃣ Aucun document à mettre à jour", "job_id": job_id}, 200

        logging.info(f"✅ {outcome.n_written}/{outcome.n_docs} documents mis à jour (harvester).")
        timer.emit({
            "role": "harvester",
            "fused_docs": outcome.n_docs,
            "own_docs": len(documents),
            **(outcome.stats or {}),
        })
        return {
            "message": f"✅ {outcome.n_written}/{outcome.n_docs} documents mis à jour",
            "job_id": job_id,
        }, 200

    except OperationalError as e:
        # Filet explicite (2026-07-29) pour le statement_timeout ci-dessus : sans lui, cette
        # exception tombait dans le catch-all générique de gcpOpsci.log_and_return_response
        # ("Il faut corriger ton code. Je force à 500.") — trompeur pour un cas connu et déjà
        # borné, pas un bug. 503 (pas 504, déjà utilisé par le timeout de Cloud Run lui-même,
        # cf. README section deadlock) signale explicitement "dépendance temporairement
        # indisponible, retry normal" plutôt qu'une erreur applicative à corriger. Toute autre
        # OperationalError (ex. perte de connexion réelle) n'est PAS ce cas connu : elle continue
        # de remonter au catch-all générique.
        if "statement timeout" not in str(e).lower():
            raise
        logging.warning(
            f"⚠️ Lecture DB interrompue par statement_timeout (120s) — probable contention DB "
            f"temporaire (cf. README). Retour 503, Cloud Tasks retentera normalement : {e}"
        )
        return {
            "message": "⚠️ Lecture DB trop longue (timeout de 120s) — nouvelle tentative via Cloud Tasks",
            "job_id": job_id,
        }, 503
    finally:
        db_gen.close()
