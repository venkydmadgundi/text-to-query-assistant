from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import Settings, SettingsError, load_settings
from services.application import (
    ApplicationInitializationError,
    ApplicationServices,
    create_application_services,
)
from tools.sql_tools import SQLResult
from agents.sql_agent import SQLAgentError
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


def display_result(result: SQLResult) -> None:
    st.subheader("Query results")
    if result.truncated:
        st.caption(f"Showing the first {result.row_count} rows.")
    else:
        st.caption(f"{result.row_count} row(s) returned.")

    frame = pd.DataFrame.from_records(result.rows, columns=result.columns)
    st.dataframe(frame, hide_index=True, alt="SQL query results")


st.set_page_config(page_title="HR Text-to-SQL Assistant", page_icon=":material/database:")
configure_logging()
try:
    settings = load_settings()
except SettingsError as exc:
    st.error(str(exc))
    st.stop()

st.title("HR Text-to-SQL assistant")
st.write(
    "Ask questions about the employee dataset in plain English. "
    "The generated SQL is checked before it runs."
)

try:
    services = get_services(
        settings.database_url,
        settings.groq_api_key,
        settings.groq_model,
        settings.max_result_rows,
    )
except ApplicationInitializationError as exc:
    st.error(str(exc))
    st.stop()

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
    run_sample = st.button("Run sample question", icon=":material/play_arrow:")

if run_sample:
    st.session_state["question_input"] = selected_question

if not settings.groq_api_key:
    st.error(
        "Groq API key is not configured. Add GROQ_API_KEY to your local .env file "
        "or environment, then restart the app."
    )

with st.form("query_form"):
    question = st.text_area(
        "Ask a question about employees",
        key="question_input",
        placeholder="For example: What is the average salary by department?",
        height=100,
    )
    submitted = st.form_submit_button("Run query", type="primary")

question_to_run = selected_question if run_sample else question if submitted else None
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
                answer = services.agent.ask(question_to_run)
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
