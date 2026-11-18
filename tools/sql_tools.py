from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from utils.logger import get_logger


logger = get_logger(__name__)
FORBIDDEN_KEYWORDS = {
    "ALTER",
    "ATTACH",
    "BEGIN",
    "CALL",
    "COMMIT",
    "COPY",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "INTO",
    "INSERT",
    "MERGE",
    "PRAGMA",
    "RELEASE",
    "REPLACE",
    "REVOKE",
    "ROLLBACK",
    "SAVEPOINT",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}


class SQLValidationError(ValueError):
    """Raised when a generated query violates the read-only SQL policy."""


class SQLExecutionError(RuntimeError):
    """Raised when an otherwise valid read-only query cannot be executed."""


@dataclass(frozen=True)
class SQLResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = False


def validate_read_only_sql(sql: str) -> None:
    if not sql or not sql.strip():
        raise SQLValidationError("The generated SQL query is empty.")

    tokens, separator_count, content_after_separator = _sql_tokens(sql)
    if separator_count > 1 or content_after_separator:
        raise SQLValidationError("Multiple SQL statements and semicolons are not allowed.")
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise SQLValidationError("Only SELECT queries are allowed.")

    forbidden = sorted(set(tokens).intersection(FORBIDDEN_KEYWORDS))
    if forbidden:
        raise SQLValidationError(
            f"Disallowed SQL operation detected: {', '.join(forbidden)}."
        )


def execute_read_only_query(
    engine: Engine,
    sql: str,
    *,
    max_rows: int = 500,
) -> SQLResult:
    validate_read_only_sql(sql)
    if max_rows < 1:
        raise ValueError("max_rows must be greater than zero.")
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise SQLExecutionError(
            "Read-only execution is configured only for SQLite and PostgreSQL."
        )

    try:
        with engine.connect() as connection:
            raw_connection = connection.connection.driver_connection
            sqlite_connection = engine.dialect.name == "sqlite"

            if sqlite_connection:
                raw_connection.execute("PRAGMA query_only=ON")
                raw_connection.set_authorizer(_sqlite_read_only_authorizer)
            else:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")

            try:
                result = connection.exec_driver_sql(sql)
                columns = list(result.keys())
                fetched_rows = [
                    dict(row)
                    for row in result.mappings().fetchmany(max_rows + 1)
                ]
            finally:
                if sqlite_connection:
                    raw_connection.set_authorizer(None)
                    raw_connection.execute("PRAGMA query_only=OFF")

        truncated = len(fetched_rows) > max_rows
        rows = fetched_rows[:max_rows]
        return SQLResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )
    except (SQLAlchemyError, sqlite3.Error) as exc:
        logger.error("SQL execution failed (%s).", type(exc).__name__)
        raise SQLExecutionError(
            "The query could not be executed. Check the generated SQL and try again."
        ) from exc


def _sqlite_read_only_authorizer(
    action: int,
    first_argument: str | None,
    second_argument: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if _trigger_name is not None:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        return (
            sqlite3.SQLITE_OK
            if first_argument == "employees" and _database_name in {None, "main"}
            else sqlite3.SQLITE_DENY
        )
    if action in {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_RECURSIVE,
    }:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (second_argument or "").lower()
        return (
            sqlite3.SQLITE_DENY
            if function_name in {"load_extension", "writefile"}
            else sqlite3.SQLITE_OK
        )
    return sqlite3.SQLITE_DENY


def _sql_tokens(sql: str) -> tuple[list[str], int, bool]:
    tokens: list[str] = []
    separator_count = 0
    content_after_separator = False
    index = 0
    length = len(sql)

    while index < length:
        character = sql[index]

        if character.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline == -1 else newline + 1
            continue
        if sql.startswith("/*", index):
            end_comment = sql.find("*/", index + 2)
            if end_comment == -1:
                raise SQLValidationError("The SQL query contains an unfinished comment.")
            index = end_comment + 2
            continue
        if character == ";":
            separator_count += 1
            index += 1
            continue
        if separator_count:
            content_after_separator = True
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            index += 1
            while index < length:
                if sql[index] == closing:
                    if closing != "]" and index + 1 < length and sql[index + 1] == closing:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if character.isalpha() or character == "_":
            start = index
            index += 1
            while index < length and (sql[index].isalnum() or sql[index] == "_"):
                index += 1
            tokens.append(sql[start:index].upper())
            continue
        index += 1

    return tokens, separator_count, content_after_separator
