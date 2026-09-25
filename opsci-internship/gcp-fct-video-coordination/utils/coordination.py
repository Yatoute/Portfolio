import gc
import logging
from collections import defaultdict

import numpy as np
from sqlalchemy.orm import Session

from utils.types import ChunkCoordinationRow, ClipIndex, SceneEmbeddingRow


def build_clip_index(rows: list[SceneEmbeddingRow]) -> ClipIndex:
    """
    Construit le clip_index à partir des lignes de get_scene_embeddings.

    La clé du dict scenes reste start_frame (utilisé par l'algo pour ordonner les scènes).
    scene_index est l'indice ordinal (0, 1, 2…) dans la vidéo, calculé à partir de
    l'ordre d'insertion (les lignes sont déjà triées par (video_id, start_frame) en SQL).
    """
    clip_index: ClipIndex = {}
    scene_counters: dict[int, int] = {}

    for row in rows:
        video_id = row["video_id"]
        if video_id not in clip_index:
            clip_index[video_id] = {"scenes": {}}
            scene_counters[video_id] = 0

        ordinal = scene_counters[video_id]
        scene_counters[video_id] += 1

        clip_index[video_id]["scenes"][row["start_frame"]] = {
            "chunk_id":        row["chunk_id"],
            "scene_index":     ordinal,
            "scene_embedding": np.asarray(row["vector"], dtype=np.float32),
        }

    return clip_index


def find_sequence_reprises(
    clip_index: ClipIndex,
    threshold: float = 0.97,
    ext_threshold: float = None,
    batch_size: int = 512,
) -> list:
    """
    Détecte toutes les séquences de scènes reprises dans le dataset indexé (embeddings CLIP).

    Gap bridging : une scène faible (sim entre ext_threshold et threshold) peut être
    incluse dans la séquence à condition que le nombre de matches stricts reste >=
    au nombre de matches faibles dans la séquence étendue.
    """
    if ext_threshold is None:
        ext_threshold = max(0.0, threshold - 0.05)
    ext_threshold = float(ext_threshold)
    logging.info(f"🎞️ Gap bridging : threshold={threshold:.3f}, ext_threshold={ext_threshold:.3f}")

    ## 1. Aplatir l'index
    scenes_flat: list    = []
    scenes_compact: list = []

    for doc_id, doc_entry in clip_index.items():
        for scene_idx, scene in doc_entry["scenes"].items():
            emb = scene.get("scene_embedding")
            if emb is None:
                continue
            scenes_flat.append({
                "doc_id":        doc_id,
                "chunk_id":      scene.get("chunk_id"),
                "scene_index":   scene.get("scene_index", 0),
                "scene_embedding": emb,
                ## raw_norm calculé après la construction de emb_matrix — rempli plus bas
                "raw_norm":      0.0,
            })
            scenes_compact.append({
                "doc_id":    doc_id,
                "scene_idx": scene_idx,
            })

    n = len(scenes_flat)
    logging.info(f"🎞️ {n} scènes à comparer ({n * (n - 1) // 2:,} paires)")
    if n < 2:
        return []

    ## 2. Préparer la matrice d'embeddings
    emb_matrix = np.asarray(
        np.stack([sf["scene_embedding"] for sf in scenes_flat]),
        dtype=np.float32,
    )
    raw_norms = np.linalg.norm(emb_matrix, axis=1)  # conservé pour le filtre de bruit visuel
    emb_matrix = emb_matrix / np.clip(raw_norms[:, None], 1e-12, None)

    for k, norm in enumerate(raw_norms):
        scenes_flat[k]["raw_norm"] = float(norm)

    ## Libérer les embeddings individuels — clip_index + scenes_flat (économie ~200 MB)
    for sf in scenes_flat:
        sf.pop("scene_embedding", None)
    for doc_entry in clip_index.values():
        for scene in doc_entry["scenes"].values():
            scene.pop("scene_embedding", None)
    gc.collect()

    doc_ids_arr = np.array([sc["doc_id"] for sc in scenes_compact], dtype=np.int64)

    ## 3. Similarités strictes par blocs imbriqués (batch_i × batch_j)
    ## Chaque bloc est au max (batch_size × batch_size) ≈ 1 MB — empreinte constante.
    ## Les matches faibles pour le gap bridging sont calculés à la demande
    ## via dot product ponctuel, sans aucun stockage préalable.
    nb            = (n + batch_size - 1) // batch_size
    n_total_blocs = nb * (nb + 1) // 2
    logging.info(f"🎞️ Calcul des similarités ({n_total_blocs} bloc(s))...")

    strict_i_parts: list = []
    strict_j_parts: list = []
    strict_s_parts: list = []

    for i_start in range(0, n, batch_size):
        i_end = min(i_start + batch_size, n)
        vec_i = emb_matrix[i_start:i_end]                   # (B_i, 512)
        d_i   = doc_ids_arr[i_start:i_end]                  # (B_i,)
        g_i   = np.arange(i_start, i_end, dtype=np.int32)   # (B_i,)

        for j_start in range(i_start, n, batch_size):
            j_end = min(j_start + batch_size, n)
            vec_j = emb_matrix[j_start:j_end]               # (B_j, 512)
            d_j   = doc_ids_arr[j_start:j_end]              # (B_j,)
            g_j   = np.arange(j_start, j_end, dtype=np.int32)  # (B_j,)

            sims  = vec_i @ vec_j.T                          # (B_i, B_j) — ≤ 1 MB

            ## Triangle supérieur cross-doc uniquement
            cross = (g_j[None, :] > g_i[:, None]) & (d_j[None, :] != d_i[:, None])

            si, sj = np.where(cross & (sims >= threshold))
            if len(si):
                strict_i_parts.append(g_i[si])
                strict_j_parts.append(g_j[sj])
                strict_s_parts.append(sims[si, sj].astype(np.float32))

            del sims, cross

        del vec_i, d_i, g_i
        gc.collect()

    n_strict = sum(len(p) for p in strict_i_parts)
    logging.info(f"🎞️ {n_strict} candidat(s) stricts | gap bridging à la demande")
    if n_strict == 0:
        del emb_matrix
        gc.collect()
        return []

    raw_i   = np.concatenate(strict_i_parts).astype(np.int32)
    raw_j   = np.concatenate(strict_j_parts).astype(np.int32)
    raw_sim = np.concatenate(strict_s_parts).astype(np.float32)
    del strict_i_parts, strict_j_parts, strict_s_parts
    gc.collect()

    ## 3b. Sim faible calculée à la demande — évite de stocker raw_weak (~1-4 GB)
    def _get_weak_sim(a: int, b: int) -> float | None:
        """Retourne la sim si ext_threshold ≤ sim < threshold, None sinon."""
        s = float(np.dot(emb_matrix[a], emb_matrix[b]))
        return s if ext_threshold <= s < threshold else None

    ## 4. Matching 1-1 par paire de documents
    pair_candidates: dict = defaultdict(list)
    for i, j, sim in zip(raw_i.tolist(), raw_j.tolist(), raw_sim.tolist()):
        doc_i    = scenes_compact[i]["doc_id"]
        doc_j    = scenes_compact[j]["doc_id"]
        pair_key = (min(doc_i, doc_j), max(doc_i, doc_j))
        pair_candidates[pair_key].append((i, j, sim))

    del raw_i, raw_j, raw_sim
    gc.collect()

    validated_matches: list = []
    n_conflicts = 0

    for candidates in pair_candidates.values():
        candidates.sort(key=lambda x: -x[2])
        used_i, used_j = set(), set()
        for i, j, sim in candidates:
            if i in used_i or j in used_j:
                n_conflicts += 1
                continue
            used_i.add(i)
            used_j.add(j)
            validated_matches.append((i, j, sim))

    logging.info(
        f"🎞️ {len(validated_matches)} match(es) validé(s) ({n_conflicts} conflit(s) 1-1 éliminés)"
    )

    if not validated_matches:
        del emb_matrix, pair_candidates
        gc.collect()
        return []

    ## 5. Ordonnancement des scènes par document
    scene_prev: dict = {}
    scene_next: dict = {}
    for doc_id, doc_entry in clip_index.items():
        s_list = sorted(doc_entry["scenes"].keys())
        for k, idx in enumerate(s_list):
            scene_prev[(doc_id, idx)] = s_list[k - 1] if k > 0 else None
            scene_next[(doc_id, idx)] = s_list[k + 1] if k + 1 < len(s_list) else None

    flat_key_to_idx = {
        (sc["doc_id"], sc["scene_idx"]): k
        for k, sc in enumerate(scenes_compact)
    }

    match_map: dict = {}
    for i, j, sim in validated_matches:
        match_map[(min(i, j), max(i, j))] = sim

    def get_match(a: int, b: int):
        return match_map.get((min(a, b), max(a, b)))

    ## 6. Identifier les séquences maximales
    sequence_pairs: list = []

    for i, j, sim in validated_matches:
        doc_a = scenes_compact[i]["doc_id"]
        si_a  = scenes_compact[i]["scene_idx"]
        doc_b = scenes_compact[j]["doc_id"]
        si_b  = scenes_compact[j]["scene_idx"]

        ## Ignorer si ce match est déjà en milieu d'une séquence couverte
        prev_a = scene_prev.get((doc_a, si_a))
        prev_b = scene_prev.get((doc_b, si_b))
        if prev_a is not None and prev_b is not None:
            pi = flat_key_to_idx.get((doc_a, prev_a))
            pj = flat_key_to_idx.get((doc_b, prev_b))
            if pi is not None and pj is not None and get_match(pi, pj) is not None:
                continue

        seq_a = [si_a]
        seq_b = [si_b]
        sims  = [sim]
        cur_a, cur_b = si_a, si_b

        n_strict = 1
        n_weak   = 0

        pending_a: list    = []
        pending_b: list    = []
        pending_sims: list = []

        while True:
            adv_a  = pending_a[-1] if pending_a else cur_a
            adv_b  = pending_b[-1] if pending_b else cur_b
            next_a = scene_next.get((doc_a, adv_a))
            next_b = scene_next.get((doc_b, adv_b))

            if next_a is None or next_b is None:
                break

            ni = flat_key_to_idx.get((doc_a, next_a))
            nj = flat_key_to_idx.get((doc_b, next_b))
            if ni is None or nj is None:
                break

            next_sim = get_match(ni, nj)

            if next_sim is not None:
                if (n_strict + 1) >= (n_weak + len(pending_a)):
                    seq_a.extend(pending_a + [next_a])
                    seq_b.extend(pending_b + [next_b])
                    sims.extend(pending_sims + [next_sim])
                    n_strict += 1
                    n_weak   += len(pending_a)
                    pending_a, pending_b, pending_sims = [], [], []
                    cur_a, cur_b = next_a, next_b
                else:
                    break
            else:
                if n_strict >= (n_weak + len(pending_a)):
                    weak_sim = _get_weak_sim(ni, nj)
                    if weak_sim is not None:
                        pending_a.append(next_a)
                        pending_b.append(next_b)
                        pending_sims.append(weak_sim)
                    else:
                        break
                else:
                    break

        best_sim = max(sims)
        sequence_pairs.append({
            "doc_a":              doc_a,
            "seq_a":              tuple(seq_a),
            "doc_b":              doc_b,
            "seq_b":              tuple(seq_b),
            "length":             len(seq_a),
            "best_similarity":    best_sim,
            "best_distance":      round(1.0 - best_sim, 4),
            "scene_similarities": sims,
        })

    logging.info(f"🎞️ {len(sequence_pairs)} séquence(s) maximale(s) identifiée(s)")
    if not sequence_pairs:
        del emb_matrix
        gc.collect()
        return []

    ## 6b. Déduplication : supprimer les sous-séquences englobées
    def _is_subseq(short: tuple, long: tuple) -> bool:
        n, m = len(short), len(long)
        if n >= m:
            return False
        for start in range(m - n + 1):
            if long[start:start + n] == short:
                return True
        return False

    by_pair: dict = defaultdict(list)
    for sp in sequence_pairs:
        by_pair[(sp["doc_a"], sp["doc_b"])].append(sp)

    deduped: list = []
    for sps in by_pair.values():
        sps.sort(key=lambda sp: -sp["length"])
        kept: list = []
        for sp in sps:
            if not any(_is_subseq(sp["seq_a"], k["seq_a"]) for k in kept):
                kept.append(sp)
        deduped.extend(kept)

    n_removed = len(sequence_pairs) - len(deduped)
    if n_removed:
        logging.info(f"🎞️ {n_removed} sous-séquence(s) englobée(s) supprimée(s)")
    sequence_pairs = deduped

    ## 7. DSU sur les occurrences
    all_occ: set = set()
    for sp in sequence_pairs:
        all_occ.add((sp["doc_a"], sp["seq_a"]))
        all_occ.add((sp["doc_b"], sp["seq_b"]))

    occ_list   = list(all_occ)
    occ_to_idx = {occ: k for k, occ in enumerate(occ_list)}
    parent     = list(range(len(occ_list)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for sp in sequence_pairs:
        union(
            occ_to_idx[(sp["doc_a"], sp["seq_a"])],
            occ_to_idx[(sp["doc_b"], sp["seq_b"])],
        )

    components: dict = defaultdict(list)
    comp_sp: dict    = defaultdict(list)

    for occ, idx in occ_to_idx.items():
        components[find(idx)].append(occ)

    for sp in sequence_pairs:
        comp_sp[find(occ_to_idx[(sp["doc_a"], sp["seq_a"])])].append(sp)

    ## 8. Construire les groupes
    groups: list = []

    for root, occs in components.items():
        doc_ids = {occ[0] for occ in occs}
        if len(doc_ids) < 2:
            continue

        sp_list   = comp_sp[root]
        best_sim  = max(sp["best_similarity"] for sp in sp_list)
        best_dist = round(1.0 - best_sim, 4)
        group_len = sp_list[0]["length"]

        sequences: list = []
        for doc_id, seq_tuple in sorted(set(occs), key=lambda x: (x[0], x[1])):
            seq_scenes = [
                scenes_flat[flat_key_to_idx[(doc_id, si)]]
                for si in seq_tuple
                if (doc_id, si) in flat_key_to_idx
            ]
            sequences.append({
                "doc_id":        doc_id,
                "scene_indices": [sf["scene_index"] for sf in seq_scenes],
                "frame_indices": list(seq_tuple),
                "scenes":        seq_scenes,
            })

        groups.append({
            "sequences":     sequences,
            "seq_pairs":     sp_list,
            "length":        group_len,
            "doc_count":     len(doc_ids),
            "best_distance": best_dist,
        })

    groups.sort(key=lambda g: (-g["length"], g["best_distance"], -g["doc_count"]))

    total_occ = sum(len(g["sequences"]) for g in groups)
    logging.info(f"🎞️ {len(groups)} groupe(s) de séquences reprises | {total_occ} occurrences")

    del emb_matrix
    gc.collect()
    return groups


def tag_noise_groups(
    groups: list,
    db: Session,
    uniform_norm_threshold: float = 0.98,
) -> list:
    """
    Tague is_noise=True les groupes de longueur 1 visuellement uniformes et sans transcription.

    Critères combinés (length=1 uniquement) :
      - raw_norm élevée (≥ uniform_norm_threshold) : keyframes quasi-identiques
        (écran noir/blanc, image de fin, pixel uniforme)
      - aucune transcription : pas de parole détectée dans la scène
    Les groupes de longueur > 1 ne sont jamais marqués comme bruit.
    Les transcriptions sont récupérées depuis la BDD uniquement pour les groupes de longueur 1.
    """
    from utils.crud_documents import get_scene_transcripts_by_chunk_ids

    ## Collecter les chunk_ids des groupes de longueur 1
    length1_chunk_ids: list[int] = [
        scene["chunk_id"]
        for g in groups if g["length"] == 1
        for seq in g["sequences"]
        for scene in seq["scenes"]
        if scene.get("chunk_id")
    ]
    transcripts = get_scene_transcripts_by_chunk_ids(db, length1_chunk_ids) if length1_chunk_ids else {}

    result = []
    for g in groups:
        is_noise = False
        if g["length"] == 1:
            all_scene_norms = [
                scene.get("raw_norm", 0.0)
                for seq in g["sequences"]
                for scene in seq["scenes"]
            ]
            visually_uniform = bool(all_scene_norms) and min(all_scene_norms) >= uniform_norm_threshold

            all_transcripts = [
                transcripts.get(scene.get("chunk_id"), "")
                for seq in g["sequences"]
                for scene in seq["scenes"]
                if scene.get("chunk_id")
            ]
            n_with = sum(1 for t in all_transcripts if t.strip())
            no_transcript = not all_transcripts or n_with / len(all_transcripts) <= 0.5

            is_noise = visually_uniform and no_transcript

        result.append({**g, "is_noise": is_noise})

    n_noise = sum(1 for g in result if g["is_noise"])
    logging.info(f"🎞️ {n_noise} groupe(s) bruités | {len(result) - n_noise} conservé(s)")
    return result


def build_coordination_rows(
    tagged_groups: list,
    clip_index: ClipIndex,
) -> list[ChunkCoordinationRow]:
    """
    Produit les lignes à insérer dans join_chunk_chunk depuis les groupes de reprises.

    Les similarités par scène sont celles calculées dans find_sequence_reprises (match_map),
    conservées dans seq_pair["scene_similarities"]. Le coordination_score pondère la
    similarité par la couverture de la séquence sur les deux vidéos.
    """
    rows: list[ChunkCoordinationRow] = []
    seen: set = set()

    for group in tagged_groups:
        if group.get("is_noise"):
            continue

        for sp in group["seq_pairs"]:
            doc_a  = sp["doc_a"]
            doc_b  = sp["doc_b"]
            length = sp["length"]

            scenes_a       = clip_index.get(doc_a, {}).get("scenes", {})
            scenes_b       = clip_index.get(doc_b, {}).get("scenes", {})
            total_scenes_a = len(scenes_a)
            total_scenes_b = len(scenes_b)
            coverage = (
                (2 * length) / (total_scenes_a + total_scenes_b)
                if (total_scenes_a + total_scenes_b) > 0 else 0.0
            )

            ## Collecter les triplets (chunk_a, chunk_b, sim) pour construire seq_a/seq_b en chunk_ids
            triplets: list[tuple[int, int, float]] = []
            for start_frame_a, start_frame_b, sim in zip(
                sp["seq_a"], sp["seq_b"], sp["scene_similarities"]
            ):
                scene_a = scenes_a.get(start_frame_a)
                scene_b = scenes_b.get(start_frame_b)
                if scene_a is None or scene_b is None:
                    continue
                triplets.append((scene_a["chunk_id"], scene_b["chunk_id"], sim))

            if not triplets:
                continue

            json_data = {
                "seq_a": [t[0] for t in triplets],
                "seq_b": [t[1] for t in triplets],
            }

            for chunk_a, chunk_b, sim in triplets:
                from_id = min(chunk_a, chunk_b)
                to_id   = max(chunk_a, chunk_b)

                key = (from_id, to_id)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    "from_chunk_id":      from_id,
                    "to_chunk_id":        to_id,
                    "type":               "coordination",
                    "similarity":         round(sim, 6),
                    "coordination_score": round(sim * coverage, 6),
                    "component_id":       "pending",
                    "json_data":          json_data,
                })

    logging.info(f"🎞️ {len(rows)} paire(s) de coordination à persister")
    return rows
