from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from langchain_groq import ChatGroq

from agents.sql_agent import SQLAgentError
from config.settings import Settings, SettingsError, load_settings
from services.application import (
    ApplicationInitializationError,
    ApplicationServices,
    create_application_services,
)
from services.document_loader import (
    DocumentLoadError,
    UploadedDocument,
)
from services.embedding_service import (
    DEFAULT_EMBEDDING_MODEL,
    SentenceTransformerEmbeddings,
)
from services.rag_service import DocumentRAGService, RAGServiceError
from services.schema_parser import SchemaParseError, parse_schema_file
from services.sql_generator import SchemaSQLGenerator, SQLGenerationError
from services.vector_store import ChromaDocumentStore, VectorStoreError
from tools.sql_tools import SQLResult
from utils.logger import configure_logging


SAMPLE_QUESTIONS = (
    "Who are the five highest-paid employees?",
    "Find employees working in the Engineering department.",
    "List employees who joined after 2022.",
    "What is the average salary by department?",
    "Which city has the highest number of employees?",
    "Who earns more than the average salary?",
    "Who reports to employee 2?",
    "Count employees in each department.",
    "Which employees joined in the last two years?",
)
APPLICATION_MODES = (
    "Text-to-SQL",
    "RAG Document Q&A",
    "Schema-to-SQL",
)


@st.cache_resource
def get_services(
    database_url: str,
    groq_api_key: str | None,
    groq_model: str,
    max_result_rows: int,
) -> ApplicationServices:
    return create_application_services(
        Settings(
            database_url=database_url,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            max_result_rows=max_result_rows,
        )
    )


@st.cache_resource
def get_groq_llm(groq_api_key: str, groq_model: str) -> ChatGroq:
    return ChatGroq(
        api_key=groq_api_key,
        model_name=groq_model,
        temperature=0,
        streaming=False,
    )


@st.cache_resource
def get_rag_service(
    groq_api_key: str,
    groq_model: str,
    chroma_persist_directory: str,
) -> DocumentRAGService:
    return DocumentRAGService(
        embeddings=SentenceTransformerEmbeddings(DEFAULT_EMBEDDING_MODEL),
        vector_store=ChromaDocumentStore(Path(chroma_persist_directory)),
        llm=get_groq_llm(groq_api_key, groq_model),
    )


@st.cache_resource
def get_schema_sql_generator(
    groq_api_key: str,
    groq_model: str,
) -> SchemaSQLGenerator:
    return SchemaSQLGenerator(get_groq_llm(groq_api_key, groq_model))


def display_result(result: SQLResult) -> None:
    st.subheader("Query results")
    if result.truncated:
        st.caption(f"Showing the first {result.row_count} rows.")
    else:
        st.caption(f"{result.row_count} row(s) returned.")

    frame = pd.DataFrame.from_records(result.rows, columns=result.columns)
    st.dataframe(frame, hide_index=True, alt="SQL query results")


def render_text_to_sql(settings: Settings) -> None:
    try:
        services = get_services(
            settings.database_url,
            settings.groq_api_key,
            settings.groq_model,
            settings.max_result_rows,
        )
    except ApplicationInitializationError as exc:
        st.error(str(exc))
        return

    with st.sidebar:
        st.header("Database")
        st.caption(f"Engine: {services.database_label}")
        st.caption("Query access: read-only")

        st.subheader("Available tables")
        for table_name in services.table_names:
            st.write(f"`{table_name}`")

        st.subheader("Employee table schema")
        schema_frame = pd.DataFrame(services.employee_schema)
        st.dataframe(
            schema_frame,
            hide_index=True,
            alt="Columns and types in the employees table",
        )

        st.subheader("Example questions")
        selected_question = st.selectbox(
            "Choose a sample question",
            SAMPLE_QUESTIONS,
            key="sample_question",
        )
        run_sample = st.button(
            "Run sample question",
            icon=":material/play_arrow:",
            key="run_sample_question",
        )

    if run_sample:
        st.session_state["question_input"] = selected_question

    if not settings.groq_api_key:
        st.error(
            "Groq API key is not configured. Locally, add GROQ_API_KEY to your .env file. "
            "On Render, add it in your web service's Environment settings, then redeploy."
        )

    with st.form("query_form"):
        question = st.text_area(
            "Ask a question about employees",
            key="question_input",
            placeholder="For example: What is the average salary by department?",
            height=100,
        )
        submitted = st.form_submit_button("Run query", type="primary")

    question_to_run = (
        selected_question if run_sample else question if submitted else None
    )
    if question_to_run is not None:
        st.session_state.pop("last_sql_result", None)
        st.session_state.pop("last_sql_error", None)
        st.session_state.pop("last_sql", None)

        if not question_to_run.strip():
            st.warning("Enter a question before running a query.")
        elif services.agent is None:
            st.error("Configure GROQ_API_KEY before submitting a question.")
        else:
            try:
                with st.spinner("Generating and checking SQL..."):
                    answer = services.agent.generate_and_execute_sql(question_to_run)
                st.session_state["last_sql_result"] = answer
            except SQLAgentError as exc:
                st.session_state["last_sql_error"] = str(exc)
                st.session_state["last_sql"] = exc.generated_sql

    answer = st.session_state.get("last_sql_result")
    if answer is not None:
        st.subheader("Generated SQL")
        st.code(answer.sql, language="sql")
        st.subheader("SQL explanation")
        st.write(answer.explanation)
        display_result(answer.result)

    error = st.session_state.get("last_sql_error")
    if error:
        generated_sql = st.session_state.get("last_sql")
        if generated_sql:
            st.subheader("Generated SQL")
            st.code(generated_sql, language="sql")
        st.error(error)


def render_rag_mode(settings: Settings) -> None:
    st.subheader("Document-based question answering")
    st.write(
        "Upload PDF, TXT, DOCX, or Markdown documents. Answers are grounded in "
        "retrieved excerpts, with source filenames shown below."
    )
    if not settings.groq_api_key:
        st.error("Configure GROQ_API_KEY before using document Q&A.")
        return

    try:
        rag_service = get_rag_service(
            settings.groq_api_key,
            settings.groq_model,
            str(settings.chroma_persist_directory),
        )
        indexed_documents = rag_service.list_documents()
    except (TypeError, ValueError, VectorStoreError) as exc:
        st.error(str(exc))
        return

    uploads = st.file_uploader(
        "Upload knowledge documents",
        type=["pdf", "txt", "docx", "md"],
        accept_multiple_files=True,
        key="rag_document_upload",
    )
    if st.button("Index uploaded documents", type="primary"):
        if not uploads:
            st.warning("Choose at least one document to index.")
        else:
            for upload in uploads:
                document = UploadedDocument(
                    filename=upload.name,
                    content=upload.getvalue(),
                )
                try:
                    result = rag_service.index_document(document)
                except (DocumentLoadError, RAGServiceError, VectorStoreError) as exc:
                    st.error(f"{upload.name}: {exc}")
                    continue
                if result.indexed:
                    st.success(
                        f"Indexed {result.filename} ({result.chunk_count} chunks)."
                    )
                else:
                    st.info(f"{result.filename} is already indexed.")
            indexed_documents = rag_service.list_documents()

    st.subheader("Indexed documents")
    if indexed_documents:
        for filename in indexed_documents:
            st.write(f"- {filename}")
    else:
        st.info("No documents have been indexed yet.")

    with st.form("rag_question_form"):
        question = st.text_area(
            "Ask a question about the uploaded documents",
            key="rag_question",
            placeholder="For example: What is the policy for annual leave?",
        )
        submitted = st.form_submit_button("Get answer")

    if submitted:
        if not question.strip():
            st.warning("Enter a question before searching the documents.")
            return
        try:
            with st.spinner("Searching documents and generating an answer..."):
                answer = rag_service.answer_from_documents(question)
        except (RAGServiceError, VectorStoreError, ValueError) as exc:
            st.error(str(exc))
            return
        st.subheader("Answer")
        st.write(answer.answer)
        if answer.sources:
            st.caption("Sources: " + ", ".join(answer.sources))


def render_schema_mode(settings: Settings) -> None:
    st.subheader("Schema-based SQL generation")
    st.write(
        "Upload a .sql, .json, or .txt schema and ask for SQL. This mode only "
        "generates SQL text; it never validates or executes the generated statement."
    )
    if not settings.groq_api_key:
        st.error("Configure GROQ_API_KEY before generating SQL.")
        return

    uploaded_schema = st.file_uploader(
        "Upload database schema",
        type=["sql", "json", "txt"],
        key="schema_upload",
    )
    schema = None
    if uploaded_schema is not None:
        try:
            schema = parse_schema_file(
                uploaded_schema.name,
                uploaded_schema.getvalue(),
            )
        except SchemaParseError as exc:
            st.error(str(exc))
        else:
            if schema.tables:
                table_names = ", ".join(table.name for table in schema.tables)
                st.caption(f"Schema loaded. Tables: {table_names}")
            else:
                st.caption("Schema description loaded.")

    with st.form("schema_sql_form"):
        question = st.text_area(
            "Describe the SQL you need",
            key="schema_sql_question",
            placeholder="For example: Find employees earning more than 50000.",
        )
        submitted = st.form_submit_button("Generate SQL", type="primary")

    if not submitted:
        return
    if schema is None:
        st.warning("Upload a valid schema file before generating SQL.")
        return
    if not question.strip():
        st.warning("Enter a request before generating SQL.")
        return

    try:
        generator = get_schema_sql_generator(
            settings.groq_api_key,
            settings.groq_model,
        )
        with st.spinner("Generating SQL from the uploaded schema..."):
            generated_sql = generator.generate_sql_only(schema.context, question)
    except (SQLGenerationError, TypeError, ValueError) as exc:
        st.error(str(exc))
        return
    st.subheader("Generated SQL")
    st.code(generated_sql, language="sql")


st.set_page_config(page_title="Text-to-SQL Assistant", page_icon=":material/database:")
configure_logging()
try:
    settings = load_settings()
except SettingsError as exc:
    st.error(str(exc))
    st.stop()

st.title("Text-to-SQL assistant")
mode = st.segmented_control(
    "Application mode",
    options=APPLICATION_MODES,
    default=None,
    key="application_mode",
)

if mode is None:
    st.info("Choose an application mode to begin.")
elif mode == "Text-to-SQL":
    st.write(
        "Ask questions about the employee dataset. Generated SQL is checked "
        "before execution."
    )
    render_text_to_sql(settings)
elif mode == "RAG Document Q&A":
    render_rag_mode(settings)
elif mode == "Schema-to-SQL":
    render_schema_mode(settings)
