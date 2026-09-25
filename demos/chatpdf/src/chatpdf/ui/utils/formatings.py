from __future__ import annotations


def pages_to_str(pages: list[int] | None) -> str:
    if not pages:
        return ""
    return ", ".join(str(p) for p in pages)


def _normalize_page_entry(page_entry) -> tuple[str, int] | None:
    """
    Supported formats:
    - int: 5
    - str: "5"
    - str range: "15-20"

    Returns:
    - (label, first_page)
      e.g. ("15-20", 15)
    """
    if isinstance(page_entry, int):
        if page_entry > 0:
            return (str(page_entry), page_entry)
        return None

    if isinstance(page_entry, str):
        raw = page_entry.strip()
        if not raw:
            return None

        if raw.isdigit():
            page = int(raw)
            if page > 0:
                return (raw, page)
            return None

        if "-" in raw:
            parts = raw.split("-", 1)
            left = parts[0].strip()
            right = parts[1].strip()
            if left.isdigit() and right.isdigit():
                start = int(left)
                end = int(right)
                if start > 0 and end >= start:
                    return (f"{start}-{end}", start)

    return None


def extract_source_page_entries(sources: list[dict] | None) -> list[dict]:
    """
    Returns unique page entries in the form:
    [
      {"label": "3", "page": 3},
      {"label": "15-20", "page": 15},
    ]
    """
    if not sources:
        return []

    seen: set[tuple[str, int]] = set()
    entries: list[dict] = []

    for s in sources:
        raw_pages = s.get("pages", []) or []
        for item in raw_pages:
            normalized = _normalize_page_entry(item)
            if not normalized:
                continue
            label, first_page = normalized
            key = (label, first_page)
            if key in seen:
                continue
            seen.add(key)
            entries.append({"label": label, "page": first_page})

    entries.sort(key=lambda x: x["page"])
    return entries


def source_files_to_text(sources: list[dict] | None) -> str:
    if not sources:
        return ""

    names = []
    for s in sources:
        fn = s.get("file_name", "document")
        if fn not in names:
            names.append(fn)

    return ", ".join(names)