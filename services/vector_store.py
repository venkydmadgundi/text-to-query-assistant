from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from utils.logger import get_logger


logger = get_logger(__name__)
COLLECTION_NAME = "uploaded_knowledge"


class VectorStoreError(RuntimeError):
    """Raised when the persistent document vector store cannot be used."""


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source_name: str
    document_hash: str
    distance: float


class ChromaDocumentStore:
    def __init__(self, persist_directory: Path) -> None:
        self._persist_directory = persist_directory
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._persist_directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self._persist_directory),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.error("Chroma initialization failed (%s).", type(exc).__name__)
            raise VectorStoreError(
                "The document vector store could not be initialized."
            ) from exc

    def close(self) -> None:
        self._client.close()

    def contains_document(self, document_hash: str) -> bool:
        try:
            existing = self._collection.get(
                where={"document_hash": document_hash},
                include=["metadatas"],
            )
        except Exception as exc:
            logger.error("Chroma lookup failed (%s).", type(exc).__name__)
            raise VectorStoreError("The document index could not be read.") from exc
        return bool(existing["ids"])

    def add_document(
        self,
        *,
        document_hash: str,
        source_name: str,
        chunks: Sequence[str],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Each document chunk must have one embedding.")
        if not chunks:
            raise ValueError("A document must contain at least one chunk.")

        ids = [f"{document_hash}:{index}" for index in range(len(chunks))]
        metadatas = [
            {
                "document_hash": document_hash,
                "source_name": source_name,
                "chunk_index": index,
            }
            for index in range(len(chunks))
        ]
        try:
            self._collection.upsert(
                ids=ids,
                documents=list(chunks),
                embeddings=[list(vector) for vector in embeddings],
                metadatas=metadatas,
            )
        except Exception as exc:
            logger.error("Chroma indexing failed (%s).", type(exc).__name__)
            raise VectorStoreError("The document could not be indexed.") from exc

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 4,
    ) -> list[RetrievedChunk]:
        if top_k < 1:
            raise ValueError("top_k must be greater than zero.")
        try:
            count = self._collection.count()
            if count == 0:
                return []
            result: dict[str, Any] = self._collection.query(
                query_embeddings=[list(query_embedding)],
                n_results=min(top_k, count),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.error("Chroma retrieval failed (%s).", type(exc).__name__)
            raise VectorStoreError("The document index could not be searched.") from exc

        documents = result["documents"][0] or []
        metadatas = result["metadatas"][0] or []
        distances = result["distances"][0] or []
        return [
            RetrievedChunk(
                text=text,
                source_name=str(metadata.get("source_name", "Unknown source")),
                document_hash=str(metadata.get("document_hash", "")),
                distance=float(distance),
            )
            for text, metadata, distance in zip(documents, metadatas, distances)
        ]

    def list_documents(self) -> list[str]:
        try:
            result = self._collection.get(include=["metadatas"])
        except Exception as exc:
            logger.error("Chroma document listing failed (%s).", type(exc).__name__)
            raise VectorStoreError("The indexed documents could not be listed.") from exc

        names = {
            str(metadata["source_name"])
            for metadata in result["metadatas"] or []
            if metadata and metadata.get("source_name")
        }
        return sorted(names, key=str.casefold)
