import os
import uuid
from pathlib import Path
from urllib.parse import urlparse
from google.cloud import storage


def parse_gs_url(gs_url: str) -> tuple[str, str]:
    if not gs_url.startswith("gs://"):
        raise ValueError(f"URL GCS invalide : {gs_url}")

    parsed = urlparse(gs_url)
    bucket_name = parsed.netloc
    blob_path = parsed.path.lstrip("/")

    if not bucket_name or not blob_path:
        raise ValueError(f"URL GCS incomplète : {gs_url}")

    return bucket_name, blob_path


def download_gcs_blob_to_tmp(
    bucket_name: str,
    blob_path: str,
    storage_client: storage.Client | None = None,
    tmp_dir: str = "/tmp",
) -> str:
    if not bucket_name:
        raise ValueError("bucket_name est obligatoire")
    if not blob_path:
        raise ValueError("blob_path est obligatoire")

    storage_client = storage_client or storage.Client()

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    if not blob.exists(storage_client):
        raise FileNotFoundError(
            f"Blob introuvable : gs://{bucket_name}/{blob_path}"
        )

    original_name = Path(blob_path).name or "video.bin"
    local_filename = f"{uuid.uuid4().hex}_{original_name}"
    local_path = os.path.join(tmp_dir, local_filename)

    blob.download_to_filename(local_path)

    return local_path


def download_gcs_url_to_tmp(
    gs_url: str,
    storage_client: storage.Client | None = None,
    tmp_dir: str = "/tmp",
) -> str:
    bucket_name, blob_path = parse_gs_url(gs_url)
    return download_gcs_blob_to_tmp(
        bucket_name=bucket_name,
        blob_path=blob_path,
        storage_client=storage_client,
        tmp_dir=tmp_dir,
    )


def cleanup_local_file(local_path: str) -> None:
    """
    Supprime le fichier local s'il existe.
    """
    try:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
    except Exception:
        pass
