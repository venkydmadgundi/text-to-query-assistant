from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from docx import Document
from langchain_core.runnables import RunnableLambda

from services.document_loader import (
    DocumentLoadError,
    UploadedDocument,
    extract_document_text,
    split_text,
)
from services.embedding_service import SentenceTransformerEmbeddings
from services.rag_service import (
    NOT_FOUND_ANSWER,
    DocumentRAGService,
)
from services.vector_store import ChromaDocumentStore


class FakeEmbeddings:
    _dimensions = {
        "leave": 0,
        "annual": 0,
        "salary": 1,
        "department": 2,
        "weather": 3,
        "policy": 4,
        "employee": 5,
    }

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @classmethod
    def _embed(cls, text: str) -> list[float]:
        vector = [0.0] * len(cls._dimensions)
        for token in set(re.findall(r"[a-z]+", text.lower())):
            dimension = cls._dimensions.get(token)
            if dimension is not None:
                vector[dimension] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class DocumentRAGTests(unittest.TestCase):
    def test_sentence_transformer_embedding_service_uses_local_model(self) -> None:
        class FakeModel:
            def __init__(self) -> None:
                self.options: dict[str, object] = {}

            def encode(self, texts: list[str], **options: object) -> list[list[float]]:
                self.options = options
                return [[0.6, 0.8] for _ in texts]

        model = FakeModel()
        embeddings = SentenceTransformerEmbeddings(model=model)

        self.assertEqual(embeddings.embed_query("annual leave"), [0.6, 0.8])
        self.assertTrue(model.options["normalize_embeddings"])
        self.assertFalse(model.options["show_progress_bar"])

    def test_extracts_text_from_txt_and_markdown(self) -> None:
        txt = extract_document_text(
            UploadedDocument("policy.txt", b"Annual leave is 20 days.")
        )
        markdown = extract_document_text(
            UploadedDocument("policy.md", b"# Policy\nAnnual leave is 20 days.")
        )

        self.assertIn("20 days", txt)
        self.assertIn("Policy", markdown)

    def test_extracts_docx_paragraphs_and_tables(self) -> None:
        document = Document()
        document.add_paragraph("Benefits policy")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Health"
        table.cell(0, 1).text = "Coverage"
        buffer = BytesIO()
        document.save(buffer)

        text = extract_document_text(
            UploadedDocument("benefits.docx", buffer.getvalue())
        )

        self.assertIn("Benefits policy", text)
        self.assertIn("Health | Coverage", text)

    def test_extracts_pdf_text(self) -> None:
        class FakePage:
            @staticmethod
            def extract_text() -> str:
                return "PDF policy text"

        class FakeReader:
            is_encrypted = False
            pages = [FakePage()]

            def __init__(self, _file: BytesIO) -> None:
                pass

        with patch("services.document_loader.PdfReader", FakeReader):
            text = extract_document_text(
                UploadedDocument("policy.pdf", b"not needed by the mocked reader")
            )

        self.assertEqual(text, "PDF policy text")

    def test_rejects_unsupported_and_empty_documents(self) -> None:
        for document in (
            UploadedDocument("image.png", b"data"),
            UploadedDocument("empty.txt", b""),
            UploadedDocument("blank.txt", b" \n "),
        ):
            with self.subTest(filename=document.filename):
                with self.assertRaises(DocumentLoadError):
                    extract_document_text(document)

    def test_chunks_respect_size_and_overlap(self) -> None:
        text = "annual leave policy " * 80
        chunks = split_text(text, chunk_size=100, chunk_overlap=20)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertTrue(any(set(chunks[index].split()) & set(chunks[index + 1].split())
                             for index in range(len(chunks) - 1)))

    def test_indexing_deduplicates_and_answers_with_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ChromaDocumentStore(Path(directory))
            try:
                calls: list[object] = []
                llm = RunnableLambda(
                    lambda prompt: calls.append(prompt)
                    or "The policy provides 20 days of annual leave."
                )
                service = DocumentRAGService(
                    embeddings=FakeEmbeddings(),
                    vector_store=store,
                    llm=llm,
                )
                document = UploadedDocument(
                    "leave.txt",
                    b"Employees receive 20 days of annual leave under the leave policy.",
                )

                first = service.index_document(document)
                duplicate = service.index_document(document)
                answer = service.answer_from_documents(
                    "How much annual leave is provided?"
                )

                self.assertTrue(first.indexed)
                self.assertGreater(first.chunk_count, 0)
                self.assertFalse(duplicate.indexed)
                self.assertEqual(service.list_documents(), ["leave.txt"])
                self.assertIn("20 days", answer.answer)
                self.assertEqual(answer.sources, ("leave.txt",))
                self.assertEqual(len(calls), 1)
            finally:
                store.close()

    def test_irrelevant_question_returns_not_found_without_calling_llm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ChromaDocumentStore(Path(directory))
            service = DocumentRAGService(
                embeddings=FakeEmbeddings(),
                vector_store=store,
                llm=RunnableLambda(
                    lambda _prompt: self.fail("LLM should not be called.")
                ),
            )
            try:
                service.index_document(
                    UploadedDocument(
                        "leave.txt",
                        b"Employees receive annual leave and follow the leave policy.",
                    )
                )
                answer = service.answer_from_documents(
                    "What is the weather forecast?"
                )
            finally:
                store.close()

        self.assertEqual(answer.answer, NOT_FOUND_ANSWER)
        self.assertEqual(answer.sources, ())

    def test_chroma_persists_vectors_between_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_store = ChromaDocumentStore(Path(directory))
            try:
                embedding = FakeEmbeddings().embed_query("annual leave policy")
                content = "Annual leave policy."
                document_hash = sha256(content.encode()).hexdigest()
                first_store.add_document(
                    document_hash=document_hash,
                    source_name="policy.txt",
                    chunks=[content],
                    embeddings=[embedding],
                )
            finally:
                first_store.close()

            reopened_store = ChromaDocumentStore(Path(directory))
            try:
                matches = reopened_store.search(embedding)
            finally:
                reopened_store.close()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].source_name, "policy.txt")
        self.assertEqual(matches[0].text, content)


if __name__ == "__main__":
    unittest.main()
