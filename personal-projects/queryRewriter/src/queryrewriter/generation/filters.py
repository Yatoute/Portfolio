import re

from queryrewriter.generation.schemas import MAX_QUERY_WORDS, MIN_QUERY_WORDS, RewriteExample, normalize_text


NOISE_PATTERNS = [
    r"\bin the paper\b",
    r"\bin this paper\b",
    r"\baccording to the paper\b",
    r"\baccording to the authors\b",
    r"\bthe paper says\b",
    r"\bthe author says\b",
]


def has_excessive_noise(text: str) -> bool:
    t = text.strip().lower()
    count = sum(1 for p in NOISE_PATTERNS if re.search(p, t))
    return count >= 2


def is_reasonable_query_length(text: str) -> bool:
    n = len(text.strip().split())
    return MIN_QUERY_WORDS <= n <= MAX_QUERY_WORDS


def filter_example(ex: RewriteExample) -> bool:
    uq = ex.user_query.strip()
    rq = ex.rewritten_query.strip()

    if not uq or not rq:
        return False

    if has_excessive_noise(rq):
        return False

    if not is_reasonable_query_length(rq):
        return False

    if not ex.should_rewrite:
        if normalize_text(uq) != normalize_text(rq):
            return False

    return True