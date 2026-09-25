"""
Text translation demo — same underlying engine as the production
`gcp-fct-libretranslate-v2` Cloud Function (Argos Translate / OPUS-MT models
served through CTranslate2), used here via Argos's standard high-level API.

Same language coverage as production too: the real `ALLOWED_LANGUAGES` set
from that Cloud Function (everything Argos Translate can translate, ~50
languages) — not a token subset. Source language is auto-detected via
fast-langdetect (fastText), exactly like production, rather than asked from
the visitor.

The production service adds a hand-built request-fusion layer
(`batch_coordinator.py`) that merges many concurrent small requests into
large GPU batches — irrelevant for a single interactive demo request, so
it's not reproduced here. The model itself, the ~50-language coverage, and
the fact that it runs in-process rather than calling an external service,
are the same.
"""

from __future__ import annotations

import argostranslate.package
import argostranslate.translate
from fast_langdetect import detect

# The exact ALLOWED_LANGUAGES set from the production translation Cloud
# Function — everything Argos Translate's public package index can
# translate, confirmed against the live index (not a hardcoded guess).
SUPPORTED_LANGUAGES: dict[str, str] = {
    "ar": "Arabe", "bg": "Bulgare", "ca": "Catalan", "cs": "Tchèque",
    "da": "Danois", "de": "Allemand", "el": "Grec", "en": "Anglais",
    "es": "Espagnol", "et": "Estonien", "eu": "Basque", "fa": "Persan",
    "fi": "Finnois", "fr": "Français", "ga": "Irlandais", "gl": "Galicien",
    "he": "Hébreu", "hi": "Hindi", "hu": "Hongrois", "id": "Indonésien",
    "it": "Italien", "ja": "Japonais", "ko": "Coréen", "ky": "Kirghize",
    "lt": "Lituanien", "lv": "Letton", "nb": "Norvégien (Bokmål)",
    "nl": "Néerlandais", "pb": "Portugais (Brésil)", "pl": "Polonais",
    "pt": "Portugais", "ro": "Roumain", "ru": "Russe", "sk": "Slovaque",
    "sl": "Slovène", "sq": "Albanais", "sv": "Suédois", "th": "Thaï",
    "tr": "Turc", "uk": "Ukrainien", "ur": "Ourdou", "vi": "Vietnamien",
    "zh": "Chinois (simplifié)", "zt": "Chinois (traditionnel)",
}

PIVOT_LANGUAGE = "en"

_installed_pairs: set[tuple[str, str]] = set()
_available_packages_cache = None


def _available_packages():
    global _available_packages_cache
    if _available_packages_cache is None:
        argostranslate.package.update_package_index()
        _available_packages_cache = argostranslate.package.get_available_packages()
    return _available_packages_cache


def _install_pair(from_code: str, to_code: str) -> None:
    pair = (from_code, to_code)
    if pair in _installed_pairs or from_code == to_code:
        return

    match = next(
        (p for p in _available_packages() if p.from_code == from_code and p.to_code == to_code),
        None,
    )
    if match is None:
        raise ValueError(f"No Argos package for {from_code} -> {to_code}")

    argostranslate.package.install_from_path(match.download())
    _installed_pairs.add(pair)


def _ensure_translation_path(from_code: str, to_code: str) -> None:
    """Direct package if Argos has one; otherwise pivot through English,
    same as production does for language pairs Argos doesn't ship directly."""
    direct_exists = any(
        p.from_code == from_code and p.to_code == to_code for p in _available_packages()
    )
    if direct_exists:
        _install_pair(from_code, to_code)
        return

    if from_code == PIVOT_LANGUAGE or to_code == PIVOT_LANGUAGE:
        raise ValueError(f"No Argos package for {from_code} -> {to_code}")

    _install_pair(from_code, PIVOT_LANGUAGE)
    _install_pair(PIVOT_LANGUAGE, to_code)


def detect_language(text: str) -> str:
    """Auto-detects the source language — same fast-langdetect (fastText)
    call used by the production translation Cloud Function."""
    result = detect(text.replace("\n", " "), model="auto", k=1)
    code = result[0]["lang"].lower()
    return code if code in SUPPORTED_LANGUAGES else PIVOT_LANGUAGE


def translate(text: str, to_code: str, from_code: str | None = None) -> tuple[str, str]:
    """Returns (translated_text, detected_or_given_source_code)."""
    if to_code not in SUPPORTED_LANGUAGES:
        raise ValueError("Unsupported target language code")

    source = from_code if from_code in SUPPORTED_LANGUAGES else detect_language(text)

    if source == to_code:
        return text, source

    _ensure_translation_path(source, to_code)
    return argostranslate.translate.translate(text, source, to_code), source
