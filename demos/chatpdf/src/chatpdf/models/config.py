from __future__ import annotations

from typing import Literal, Optional

from dataclasses import dataclass
import os
from dotenv import load_dotenv

ModelProvider = Literal["ollama", "openai"]

load_dotenv()

def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return float(v)


@dataclass(frozen=True)
class ModelsConfig:
    # Providers
    embed_model_provider: ModelProvider = os.getenv("EMBED_MODEL_PROVIDER", "openai")
    chat_model_provider: ModelProvider = os.getenv("CHAT_MODEL_PROVIDER", "openai")

    # Ollama
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:0.8b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    ollama_temperature: float = _float("OLLAMA_TEMPERATURE", 0.0)

    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-5-nano")
    openai_embed_model: str = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    openai_temperature: float = _float("OPENAI_TEMPERATURE", 0.0)
    
    @property
    def embed_model(self) -> Optional[str]:
        if self.embed_model_provider == "ollama":
            return self.ollama_embed_model
        elif self.embed_model_provider == "openai":
            return self.openai_embed_model
        raise None

    @property
    def chat_model(self) -> Optional[str]:
        if self.chat_model_provider == "ollama":
            return self.ollama_chat_model
        elif self.chat_model_provider == "openai":
            return self.openai_chat_model
        raise None