from __future__ import annotations

from typing import Optional

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings

from chatpdf.models.config import ModelsConfig, ModelProvider


class ModelsFactory:
    """
    Factory simple:
    - provider = "ollama" ou "openai"
    - construit chat LLM + embeddings
    - cache les instances pour éviter de recréer les clients

    Usage:
        mf = ModelsFactory()
        llm = mf.chat_llm(provider="openai")
        emb = mf.embeddings(provider="ollama")
    """

    def __init__(self, config: Optional[ModelsConfig] = None) -> None:
        self.config = config or ModelsConfig()

        self._cached_ollama_llm: Optional[BaseChatModel] = None
        self._cached_openai_llm: Optional[BaseChatModel] = None
        self._cached_ollama_emb: Optional[Embeddings] = None
        self._cached_openai_emb: Optional[Embeddings] = None

    def chat_llm(self, provider: Optional[ModelProvider] = None) -> BaseChatModel:
        p = provider or self.config.chat_model_provider
        if p == "ollama":
            return self._ollama_llm()
        if p == "openai":
            return self._openai_llm()
        raise ValueError("provider doit être 'ollama' ou 'openai'.")

    def embeddings(self, provider: Optional[ModelProvider] = None) -> Embeddings:
        p = provider or self.config.embed_model_provider
        if p == "ollama":
            return self._ollama_embeddings()
        if p == "openai":
            return self._openai_embeddings()
        raise ValueError("provider doit être 'ollama' ou 'openai'.")

    # -----------------------
    # Internals
    # -----------------------

    def _ollama_llm(self) -> BaseChatModel:
        if self._cached_ollama_llm is None:
            c = self.config
            self._cached_ollama_llm = ChatOllama(
                base_url=c.ollama_base_url,
                model=c.ollama_chat_model,
                temperature=c.ollama_temperature,
            )
        return self._cached_ollama_llm

    def _ollama_embeddings(self) -> Embeddings:
        if self._cached_ollama_emb is None:
            c = self.config
            self._cached_ollama_emb = OllamaEmbeddings(
                base_url=c.ollama_base_url,
                model=c.ollama_embed_model,
            )
        return self._cached_ollama_emb

    def _openai_llm(self) -> BaseChatModel:
        if self._cached_openai_llm is None:
            c = self.config
            if not c.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY manquante.")
            self._cached_openai_llm = ChatOpenAI(
                api_key=c.openai_api_key,
                model=c.openai_chat_model,
                temperature=c.openai_temperature,
            )
        return self._cached_openai_llm

    def _openai_embeddings(self) -> Embeddings:
        if self._cached_openai_emb is None:
            c = self.config
            if not c.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY manquante.")
            self._cached_openai_emb = OpenAIEmbeddings(
                api_key=c.openai_api_key,
                model=c.openai_embed_model,
            )
        return self._cached_openai_emb
