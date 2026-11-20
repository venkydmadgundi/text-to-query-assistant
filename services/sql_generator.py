from __future__ import annotations

import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from prompts.schema_sql_prompts import SCHEMA_SQL_PROMPT
from utils.logger import get_logger


logger = get_logger(__name__)


class SQLGenerationError(RuntimeError):
    """Raised when the model cannot generate SQL from an uploaded schema."""


class SchemaSQLGenerator:
    def __init__(self, llm: Runnable) -> None:
        self._chain = SCHEMA_SQL_PROMPT | llm | StrOutputParser()

    def generate_sql_only(self, schema_context: str, question: str) -> str:
        if not schema_context.strip():
            raise ValueError("Upload a schema before generating SQL.")
        if not question.strip():
            raise ValueError("Enter a question before generating SQL.")

        try:
            output = self._chain.invoke(
                {
                    "schema": schema_context,
                    "question": question.strip(),
                }
            )
        except Exception as exc:
            logger.error("Schema SQL generation failed (%s).", type(exc).__name__)
            raise SQLGenerationError(
                "SQL could not be generated from the uploaded schema."
            ) from exc
        return _remove_markdown_fence(output.strip())


def _remove_markdown_fence(output: str) -> str:
    fenced_sql = re.fullmatch(
        r"```(?:sql)?\s*(.*?)\s*```",
        output,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return fenced_sql.group(1).strip() if fenced_sql else output
