from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Sequence

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from prompts.rag_prompts import RAG_ANSWER_PROMPT
from services.document_loader import (
    DocumentLoadError,
    UploadedDocument,
    extract_document_text,
    split_text,
)
from services.embedding_service import EmbeddingProvider
from services.vector_store import ChromaDocumentStore, RetrievedChunk, VectorStoreError
from utils.logger import get_logger


logger = get_logger(__name__)
NOT_FOUND_ANSWER = "The uploaded documents do not contain relevant information to answer this question."


class RAGServiceError(RuntimeError):
    """Raised when document indexing or grounded answering fails."""


@dataclass(frozen=True)
class IndexingResult:
    filename: str
    indexed: bool
    chunk_count: int


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    sources: tuple[str, ...]


class DocumentRAGService:
    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        vector_store: ChromaDocumentStore,
        llm: Runnable,
        top_k: int = 4,
        minimum_similarity: float = 0.25,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be greater than zero.")
        if not 0.0 <= minimum_similarity <= 1.0:
            raise ValueError("minimum_similarity must be between zero and one.")
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._chain = RAG_ANSWER_PROMPT | llm | StrOutputParser()
        self._top_k = top_k
        self._minimum_similarity = minimum_similarity

    def index_document(self, document: UploadedDocument) -> IndexingResult:
        text = extract_document_text(document)
        chunks = split_text(text)
        if not chunks:
            raise DocumentLoadError(
                f"No indexable text was found in {document.filename!r}."
            )

        document_hash = hashlib.sha256(document.content).hexdigest()
        try:
            if self._vector_store.contains_document(document_hash):
                return IndexingResult(
                    filename=document.filename,
                    indexed=False,
                    chunk_count=0,
                )
            vectors = self._embeddings.embed_documents(chunks)
            self._vector_store.add_document(
                document_hash=document_hash,
                source_name=document.filename,
                chunks=chunks,
                embeddings=vectors,
            )
        except VectorStoreError:
            raise
        except Exception as exc:
            logger.error("Document embedding failed (%s).", type(exc).__name__)
            raise RAGServiceError(
                f"{document.filename!r} could not be embedded and indexed."
            ) from exc
        return IndexingResult(
            filename=document.filename,
            indexed=True,
            chunk_count=len(chunks),
        )

    def list_documents(self) -> list[str]:
        return self._vector_store.list_documents()

    def answer_from_documents(self, question: str) -> RAGAnswer:
        if not question.strip():
            raise ValueError("Enter a question before searching the documents.")

        try:
            if not self._vector_store.list_documents():
                return RAGAnswer(NOT_FOUND_ANSWER, ())
            query_embedding = self._embeddings.embed_query(question.strip())
            retrieved = self._vector_store.search(
                query_embedding,
                top_k=self._top_k,
            )
        except (VectorStoreError, RAGServiceError):
            raise
        except Exception as exc:
            logger.error("Document retrieval failed (%s).", type(exc).__name__)
            raise RAGServiceError(
                "The uploaded documents could not be searched."
            ) from exc

        relevant = [
            chunk
            for chunk in retrieved
            if 1.0 - chunk.distance >= self._minimum_similarity
        ]
        if not relevant:
            return RAGAnswer(NOT_FOUND_ANSWER, ())

        context = _format_context(relevant)
        try:
            answer = self._chain.invoke(
                {"context": context, "question": question.strip()}
            ).strip()
        except Exception as exc:
            logger.error("Document answer generation failed (%s).", type(exc).__name__)
            raise RAGServiceError(
                "An answer could not be generated from the uploaded documents."
            ) from exc

        sources = tuple(dict.fromkeys(chunk.source_name for chunk in relevant))
        return RAGAnswer(answer=answer, sources=sources)


def _format_context(chunks: Sequence[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[Source: {chunk.source_name}]\n{chunk.text}" for chunk in chunks
    )
