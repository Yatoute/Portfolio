import os
import time
import psutil
import gc
import ctypes
import logging
import shutil


def free_rss_memory():
    """Rend au système d'exploitation les pages heap libres accumulées par glibc.

    Python et NumPy ne retournent pas spontanément la mémoire au système après
    des `del` — le RSS process reste élevé. malloc_trim(0) force ce retour.
    À appeler après chaque document traité.
    """
    gc.collect()
    try:
        ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def cleanup_stale_tmp_files(max_age_seconds: int = 600) -> int:
    """Supprime les fichiers vidéo /tmp non nettoyés par des requêtes précédentes.

    Cloud Functions avec max_instance_request_concurrency=1 traite les requêtes
    séquentiellement : tout fichier /tmp restant appartient forcément à une requête passée.
    À appeler au démarrage de chaque requête worker.
    """
    removed = 0
    try:
        now = time.time()
        for fname in os.listdir("/tmp"):
            if not (fname.endswith(".mp4") or fname.endswith(".MP4")):
                continue
            fpath = os.path.join("/tmp", fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age > max_age_seconds:
                    os.remove(fpath)
                    removed += 1
                    logging.info(f"🧹 /tmp stale supprimé : {fname} (age={age:.0f}s)")
            except Exception:
                pass
    except Exception:
        pass
    if removed:
        free_rss_memory()
    return removed


def log_memory(stage: str):
    try:
        process = psutil.Process(os.getpid())

        mem_info = process.memory_info()
        rss_mb = mem_info.rss / 1024 / 1024

        _, tmp_used, tmp_free = shutil.disk_usage("/tmp")
        tmp_used_mb = tmp_used / 1024 / 1024
        tmp_free_mb = tmp_free / 1024 / 1024

        logging.info(
            f"🧠 MEMORY | stage={stage} | "
            f"process_rss_mb={rss_mb:.2f} | "
            f"/tmp_used_mb={tmp_used_mb:.2f} | /tmp_free_mb={tmp_free_mb:.2f}"
        )

    except Exception as e:
        logging.warning(f"🧠 MEMORY LOG FAILED | stage={stage} | error={e}")
