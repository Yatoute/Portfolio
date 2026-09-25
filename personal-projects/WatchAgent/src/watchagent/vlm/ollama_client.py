from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import ollama

from watchagent.settings import (
    OLLAMA_BASE_URL,
    OLLAMA_JPEG_QUALITY,
    OLLAMA_MAX_RESOLUTION,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
)

# Type alias : un tour = (message utilisateur, réponse assistant)
PriorTurn = tuple[str, str]


class OllamaVLMClient:
    """
    Client Ollama basé sur le SDK officiel (ollama-python).

    Les frames sont sauvegardées en fichiers JPEG temporaires et leur chemin
    est passé directement au SDK. Cela évite de construire un payload JSON
    en mémoire avec les images encodées en base64, ce qui était la cause
    principale des timeouts avec urllib.

    Le SDK ollama utilise httpx avec un read-timeout illimité par défaut,
    contrairement à urllib où le timeout s'applique à la fois à la connexion
    ET à la lecture de la réponse.

    Args:
        base_url:        URL du serveur Ollama (ex. "http://localhost:11434").
        model:           Nom du modèle vision à utiliser.
        jpeg_quality:    Qualité JPEG des fichiers temporaires (1–100).
        max_resolution:  Redimensionne les frames de sorte que la plus grande
                         dimension ne dépasse pas cette valeur (px). 0 = désactivé.
        temperature:     Température d'inférence. 0 = décodage glouton (plus
                         rapide et déterministe, recommandé pour la surveillance).
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        jpeg_quality: int = OLLAMA_JPEG_QUALITY,
        max_resolution: int = OLLAMA_MAX_RESOLUTION,
        temperature: float = OLLAMA_TEMPERATURE,
    ) -> None:
        self.model = model
        self.jpeg_quality = jpeg_quality
        self.max_resolution = max_resolution
        self.temperature = temperature
        self._client = ollama.Client(host=base_url)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def describe_scene(
        self,
        frames: list[np.ndarray],
        prompt: str,
        system: Optional[str] = None,
        prior_turns: Optional[list[PriorTurn]] = None,
    ) -> str:
        """
        Envoie les frames au modèle Ollama et retourne la description.

        Les frames sont sauvegardées en fichiers JPEG temporaires, passées au
        SDK via leurs chemins, puis supprimées dans le bloc ``finally``.

        Args:
            frames:      Frames BGR dans l'ordre chronologique (scène courante).
            prompt:      Instruction textuelle pour la scène courante.
            system:      Prompt système optionnel.
            prior_turns: Historique des scènes précédentes sous forme de paires
                         (user_message, assistant_description). Injecté avant le
                         message de la scène courante, sans images.

        Returns:
            Texte de description généré par le modèle.

        Raises:
            RuntimeError: En cas d'erreur Ollama.
        """
        image_paths = [self._save_temp_frame(f) for f in frames]

        try:
            messages: list[dict] = []

            if system:
                messages.append({"role": "system", "content": system})

            # Scènes précédentes : tours texte uniquement (pas d'images)
            for user_msg, assistant_msg in (prior_turns or []):
                messages.append({"role": "user", "content": user_msg})
                messages.append({"role": "assistant", "content": assistant_msg})

            # Scène courante avec chemins des images temporaires
            messages.append({
                "role": "user",
                "content": prompt,
                "images": image_paths,
            })

            response = self._client.chat(
                model=self.model,
                messages=messages,
                options={"temperature": self.temperature},
            )
            return response["message"]["content"]

        except ollama.ResponseError as exc:
            raise RuntimeError(
                f"Ollama erreur modèle ({self.model}): {exc.error}"
            ) from exc

        finally:
            for path in image_paths:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _save_temp_frame(self, frame: np.ndarray) -> str:
        """
        Redimensionne (si nécessaire) et sauvegarde une frame BGR en JPEG temp.

        Returns:
            Chemin absolu du fichier temporaire créé.
        """
        if self.max_resolution > 0:
            h, w = frame.shape[:2]
            if max(h, w) > self.max_resolution:
                scale = self.max_resolution / max(h, w)
                frame = cv2.resize(
                    frame,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp_path = tmp.name
        tmp.close()
        cv2.imwrite(tmp_path, frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return tmp_path
