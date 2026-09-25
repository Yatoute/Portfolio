#!/usr/bin/env python3
"""Cartographie récursive des chemins non-nuls d'un objet JSON.

Utile pour comparer la structure d'un nœud brut retourné par just_scrape=True
avec les chemins attendus dans parser.py (diagnostic Level 1).

Usage:
    python find_paths.py <json_file>
    echo '{"a": {"b": 1}}' | python find_paths.py -
    python find_paths.py <json_file> [depth=10]

Exemple:
    python find_paths.py /tmp/raw_node.json
"""
import sys
import json


def find_paths(obj, path: str = "", depth: int = 0, max_depth: int = 10):
    """ Affiche récursivement tous les chemins non-nuls d'un objet JSON """
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if v is not None and v != "" and v != [] and v != {}:
                print(f"{p} = {repr(v)[:120]}")
            find_paths(v, p, depth + 1, max_depth)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):  # limiter à 5 éléments par liste
            find_paths(v, f"{path}[{i}]", depth + 1, max_depth)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_paths.py <json_file|-> [max_depth]", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    if src == "-":
        data = json.load(sys.stdin)
    else:
        with open(src) as f:
            data = json.load(f)

    # Si la donnée est une liste, traiter le premier élément
    if isinstance(data, list):
        if not data:
            print("Liste vide", file=sys.stderr)
            sys.exit(1)
        print(f"[find_paths] Liste de {len(data)} éléments — analyse du premier :\n")
        data = data[0]

    find_paths(data, max_depth=max_depth)
