from __future__ import annotations

from pathlib import Path
from typing import Optional, Literal, Any, Dict, List, Type, TypeVar

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_community.vectorstores import FAISS

T = TypeVar("T", bound="FaissStore")


class FaissStore:
    def __init__(self, embeddings: Embeddings) -> None:
        self._embeddings = embeddings
        self._store: Optional[FAISS] = None
        self._graph_index: Dict[str, Document] = {}

    @property
    def store(self) -> FAISS:
        if self._store is None:
            raise RuntimeError("FAISSStore n'est pas initialisé. Appelle build() ou load().")
        return self._store
    
    @classmethod
    def from_embeddings(
        cls: Type[T], 
        *,
        docs: list[Document], 
        vectors: list[list[float]], 
        embeddings: Embeddings,
        build_graph_index: bool = True
    ) -> T:
        """
        Build Faiss store à partir de documents et vecteurs.

        Usage:
            emb = ModelsFactory().embeddings(provider="ollama")
            store = FaissStore.from_embeddings(docs=docs, vectors=vectors, embeddings=emb)
        """
        texts = [doc.page_content for doc in docs]
        metadatas = [doc.metadata for doc in docs]
        text_embeddings = list(zip(texts, vectors))
        
        inst = cls(embeddings=embeddings)
        inst._store = FAISS.from_embeddings(
            text_embeddings=text_embeddings,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        if build_graph_index:
            inst._build_graph_index()
            
        return inst
    
    @classmethod
    def from_index(
        cls: Type[T],
        *,
        embeddings: Embeddings,
        dir_path: str | Path,
        build_graph_index: bool = True,
    ) -> T:
        """
        Build Faiss store à partir d'un index FAISS existant.

        Usage:
            emb = ModelsFactory().embeddings(provider="ollama")
            store = FaissStore.from_index(embeddings=emb, dir_path="./outputs/faiss_index")
        """
        inst = cls(embeddings=embeddings)
        inst.load(dir_path=dir_path, build_graph_index=build_graph_index)
        return inst
    
    @classmethod
    def from_dir(
        cls: Type[T],
        *,
        embeddings: Embeddings,
        dir_path: str | Path,
        build_graph_index: bool = True,
    ) -> T:
        """Alias de from_index()."""
        return cls.from_index(
            embeddings=embeddings,
            dir_path=dir_path,
            build_graph_index=build_graph_index,
        )


    def build(self, docs: list[Document], build_graph_index: bool = True, **kwargs: Any) -> "FaissStore":
        if not docs:
            raise ValueError("Impossible de construire FAISS avec une liste de docs vide.")

        if not kwargs.get("normalize_L2"):
            kwargs["normalize_L2"] = True

        self._store = FAISS.from_documents(documents=docs, embedding=self._embeddings, **kwargs)

        if build_graph_index:
            self._build_graph_index()

        return self

    def _build_graph_index(self) -> None:
        self._graph_index = {}
        for doc in self._store.docstore._dict.values():  # type: ignore
            chunk_id = doc.metadata.get("chunk_id")
            if chunk_id:
                self._graph_index[chunk_id] = doc

    def save(self, dir_path: str | Path) -> None:
        path = Path(dir_path)
        path.mkdir(parents=True, exist_ok=True)
        self.store.save_local(str(path))

    def load(self, dir_path: str | Path, build_graph_index: bool = True) -> "FaissStore":
        path = Path(dir_path)
        if not path.exists():
            raise FileNotFoundError(f"Dossier FAISS introuvable: {path}")

        self._store = FAISS.load_local(
            folder_path=str(path),
            embeddings=self._embeddings,
            allow_dangerous_deserialization=True,
        )

        if build_graph_index:
            self._build_graph_index()

        return self

    def as_retriever(
        self,
        *,
        k: int = 3,
        search_type: Literal["similarity", "mmr"] = "mmr",
        **kwargs: Any,
    ) -> VectorStoreRetriever:
        if search_type not in {"similarity", "mmr"}:
            raise ValueError("search_type doit être 'similarity' ou 'mmr'.")

        kwargs["k"] = k
        if search_type == "mmr":
            kwargs.update({"fetch_k": max(20, k * 5), "lambda_mult": 0.5})

        return self.store.as_retriever(search_type=search_type, search_kwargs=kwargs)

    def fetch_next(self, chunk_id: str) -> Optional[Document]:
        doc = self._graph_index.get(chunk_id)
        if not doc:
            return None
        next_id = doc.metadata.get("next_chunk_id")
        return self._graph_index.get(next_id)

    def fetch_previous(self, chunk_id: str) -> Optional[Document]:
        doc = self._graph_index.get(chunk_id)
        if not doc:
            return None
        prev_id = doc.metadata.get("prev_chunk_id")
        return self._graph_index.get(prev_id)

    def fetch_window(self, chunk_id: str, n_before: int = 1, n_after: int = 2) -> List[Document]:
        results: List[Document] = []
        current = self._graph_index.get(chunk_id)

        temp = current
        for _ in range(n_before):
            if not temp:
                break
            temp = self.fetch_previous(temp.metadata["chunk_id"])
            if temp:
                results.insert(0, temp)

        if current:
            results.append(current)

        temp = current
        for _ in range(n_after):
            if not temp:
                break
            temp = self.fetch_next(temp.metadata["chunk_id"])
            if temp:
                results.append(temp)

        return results