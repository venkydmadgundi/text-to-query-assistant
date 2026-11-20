from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq
from sqlalchemy.engine import Engine

from config.settings import Settings
from database.schema import get_employee_schema
from prompts.sql_prompts import SQL_GENERATION_PROMPT
from tools.sql_tools import (
    SQLResult,
    SQLValidationError,
    execute_read_only_query,
    validate_read_only_sql,
)
from utils.logger import get_logger


logger = get_logger(__name__)


class SQLAgentError(RuntimeError):
    """An expected error while generating, validating, or running SQL."""

    def __init__(self, message: str, generated_sql: str | None = None) -> None:
        super().__init__(message)
        self.generated_sql = generated_sql


@dataclass(frozen=True)
class SQLAnswer:
    sql: str
    explanation: str
    result: SQLResult


class TextToSQLAgent:
    """Generate SQL with a LangChain runnable and execute it through a guard."""

    def __init__(
        self,
        *,
        llm: Runnable,
        engine: Engine,
        schema: str,
        max_result_rows: int = 500,
    ) -> None:
        self._engine = engine
        self._schema = schema
        self._max_result_rows = max_result_rows
        self._chain = (
            SQL_GENERATION_PROMPT | llm | StrOutputParser()
        )

    def ask(self, question: str) -> SQLAnswer:
        if not question.strip():
            raise SQLAgentError("Enter a question before running a query.")

        try:
            generated_sql = self._chain.invoke(
                {
                    "schema": self._schema,
                    "question": question.strip(),
                    "today": date.today().isoformat(),
                }
            )
        except Exception as exc:
            logger.error("SQL generation failed (%s).", type(exc).__name__)
            raise SQLAgentError(
                "The SQL query could not be generated. Check your Groq configuration "
                "and try again."
            ) from exc

        sql = _clean_generated_sql(generated_sql)
        try:
            validate_read_only_sql(sql)
        except SQLValidationError as exc:
            raise SQLAgentError(
                f"The generated SQL was blocked: {exc}",
                generated_sql=sql,
            ) from exc

        try:
            result = execute_read_only_query(
                self._engine,
                sql,
                max_rows=self._max_result_rows,
            )
        except SQLValidationError as exc:
            raise SQLAgentError(
                f"The generated SQL was blocked: {exc}",
                generated_sql=sql,
            ) from exc
        except RuntimeError as exc:
            raise SQLAgentError(str(exc), generated_sql=sql) from exc

        return SQLAnswer(
            sql=sql,
            explanation=explain_sql(sql),
            result=result,
        )

    def generate_and_execute_sql(self, question: str) -> SQLAnswer:
        return self.ask(question)


def create_text_to_sql_agent(settings: Settings, engine: Engine) -> TextToSQLAgent:
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is required to create the SQL agent.")

    llm = ChatGroq(
        api_key=settings.groq_api_key,
        model_name=settings.groq_model,
        temperature=0,
        streaming=True,
    )
    return TextToSQLAgent(
        llm=llm,
        engine=engine,
        schema=get_employee_schema(engine),
        max_result_rows=settings.max_result_rows,
    )


def _clean_generated_sql(output: str) -> str:
    sql = output.strip()
    fenced_sql = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", sql, re.IGNORECASE | re.DOTALL)
    if fenced_sql:
        sql = fenced_sql.group(1).strip()
    return sql


def explain_sql(sql: str) -> str:
    """Give a concise, deterministic explanation of common SQL clauses."""
    normalized = " ".join(sql.lower().split())
    parts = ["Reads employee data"]

    if re.search(r"\bcount\s*\(", normalized):
        parts.append("calculates a count")
    if re.search(r"\bavg\s*\(", normalized):
        parts.append("calculates an average")
    if re.search(r"\bsum\s*\(", normalized):
        parts.append("calculates a total")
    if re.search(r"\bwhere\b", normalized):
        parts.append("filters rows with a WHERE condition")
    group_by = re.search(r"\bgroup\s+by\s+(.+?)(?:\border\s+by\b|\blimit\b|$)", normalized)
    if group_by:
        parts.append(f"groups the results by {group_by.group(1).strip()}")
    if re.search(r"\border\s+by\b", normalized):
        parts.append("sorts the results")
    limit = re.search(r"\blimit\s+(\d+)", normalized)
    if limit:
        parts.append(f"limits the output to {limit.group(1)} rows")

    return "; ".join(parts) + "."
