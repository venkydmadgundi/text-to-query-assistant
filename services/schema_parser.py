from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError


SUPPORTED_SCHEMA_SUFFIXES = {".sql", ".json", ".txt"}


class SchemaParseError(ValueError):
    """Raised when an uploaded schema file cannot be interpreted."""


@dataclass(frozen=True)
class SchemaColumn:
    name: str
    data_type: str
    primary_key: bool = False
    foreign_key: str | None = None


@dataclass(frozen=True)
class SchemaTable:
    name: str
    columns: tuple[SchemaColumn, ...]


@dataclass(frozen=True)
class DatabaseSchema:
    context: str
    tables: tuple[SchemaTable, ...]


def parse_schema_file(filename: str, content: bytes) -> DatabaseSchema:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SCHEMA_SUFFIXES:
        raise SchemaParseError(
            f"Unsupported schema format for {filename!r}. Use .sql, .json, or .txt."
        )
    if not content:
        raise SchemaParseError(f"{filename!r} is empty.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SchemaParseError(f"{filename!r} must be UTF-8 encoded.") from exc
    if not text.strip():
        raise SchemaParseError(f"{filename!r} does not contain a schema.")

    if suffix == ".sql":
        return _parse_sql_schema(text)
    if suffix == ".json":
        return _parse_json_schema(text)
    return _parse_text_schema(text)


def _parse_sql_schema(text: str) -> DatabaseSchema:
    try:
        statements = [
            statement
            for statement in parse(text)
            if statement is not None
        ]
    except ParseError as exc:
        raise SchemaParseError("The SQL schema could not be parsed.") from exc

    tables: list[SchemaTable] = []
    ddl_statements: list[str] = []
    for statement in statements:
        if not isinstance(statement, exp.Create) or statement.args.get("kind") != "TABLE":
            continue
        table_definition = statement.this
        if isinstance(table_definition, exp.Schema):
            table_expression = table_definition.this
            definitions = table_definition.expressions
        else:
            table_expression = table_definition
            definitions = []

        table_name = _expression_name(table_expression)
        if not table_name:
            continue
        column_definitions = [
            definition
            for definition in definitions
            if isinstance(definition, exp.ColumnDef)
        ]
        primary_keys, foreign_keys = _extract_sql_constraints(definitions)
        columns = tuple(
            SchemaColumn(
                name=definition.this.name,
                data_type=definition.args.get("kind").sql()
                if definition.args.get("kind") is not None
                else "UNKNOWN",
                primary_key=(
                    definition.this.name.casefold() in primary_keys
                    or bool(definition.find(exp.PrimaryKeyColumnConstraint))
                ),
                foreign_key=foreign_keys.get(definition.this.name.casefold()),
            )
            for definition in column_definitions
        )
        tables.append(SchemaTable(name=table_name, columns=columns))
        ddl_statements.append(statement.sql())

    if not tables:
        raise SchemaParseError(
            "The .sql file must contain at least one CREATE TABLE statement."
        )

    context = _format_schema_context(
        "\n;\n".join(ddl_statements),
        tables,
    )
    return DatabaseSchema(context=context, tables=tuple(tables))


def _extract_sql_constraints(
    definitions: list[exp.Expression],
) -> tuple[set[str], dict[str, str]]:
    primary_keys: set[str] = set()
    foreign_keys: dict[str, str] = {}
    for definition in definitions:
        sql = definition.sql()
        primary_match = re.search(
            r"\bPRIMARY\s+KEY\s*\(([^)]+)\)",
            sql,
            flags=re.IGNORECASE,
        )
        if primary_match:
            primary_keys.update(
                _unquote_identifier(value).casefold()
                for value in primary_match.group(1).split(",")
            )

        foreign_match = re.search(
            r"\bFOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+"
            r"([^\s(]+)\s*\(([^)]+)\)",
            sql,
            flags=re.IGNORECASE,
        )
        if foreign_match:
            target_table = _unquote_identifier(foreign_match.group(2))
            target_columns = [
                _unquote_identifier(value)
                for value in foreign_match.group(3).split(",")
            ]
            for source, target in zip(
                foreign_match.group(1).split(","),
                target_columns,
            ):
                foreign_keys[_unquote_identifier(source).casefold()] = (
                    f"{target_table}.{target}"
                )

        inline_foreign_match = re.search(
            r"\bREFERENCES\s+([^\s(]+)\s*\(([^)]+)\)",
            sql,
            flags=re.IGNORECASE,
        )
        if inline_foreign_match and isinstance(definition, exp.ColumnDef):
            foreign_keys[definition.this.name.casefold()] = (
                f"{_unquote_identifier(inline_foreign_match.group(1))}."
                f"{_unquote_identifier(inline_foreign_match.group(2))}"
            )

    return primary_keys, foreign_keys


def _parse_json_schema(text: str) -> DatabaseSchema:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaParseError("The JSON schema is not valid JSON.") from exc

    raw_tables: Any = payload.get("tables") if isinstance(payload, dict) else None
    if isinstance(raw_tables, dict):
        table_entries = [
            {"name": name, **(value if isinstance(value, dict) else {})}
            for name, value in raw_tables.items()
        ]
    elif isinstance(raw_tables, list):
        table_entries = raw_tables
    else:
        raise SchemaParseError(
            "JSON schema must contain a 'tables' object or array."
        )

    tables: list[SchemaTable] = []
    for entry in table_entries:
        if not isinstance(entry, dict):
            raise SchemaParseError("Each JSON table definition must be an object.")
        name = str(entry.get("name") or entry.get("table_name") or "").strip()
        raw_columns = entry.get("columns")
        if not name or not isinstance(raw_columns, (list, dict)):
            raise SchemaParseError(
                "Each JSON table requires a name and a columns array or object."
            )
        column_entries = (
            [
                {"name": column_name, **(value if isinstance(value, dict) else {})}
                for column_name, value in raw_columns.items()
            ]
            if isinstance(raw_columns, dict)
            else raw_columns
        )
        table_primary_keys = entry.get("primary_key", [])
        if isinstance(table_primary_keys, str):
            table_primary_keys = [table_primary_keys]
        if not isinstance(table_primary_keys, list):
            table_primary_keys = []
        table_foreign_keys = _json_foreign_keys(entry.get("foreign_keys", []))
        columns: list[SchemaColumn] = []
        for column in column_entries:
            if not isinstance(column, dict):
                raise SchemaParseError("Each JSON column definition must be an object.")
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                raise SchemaParseError(f"Table {name!r} contains a column without a name.")
            foreign_key = (
                column.get("foreign_key")
                or column.get("references")
                or column.get("ref")
            )
            columns.append(
                SchemaColumn(
                    name=column_name,
                    data_type=str(column.get("type") or column.get("data_type") or "UNKNOWN"),
                    primary_key=(
                        bool(column.get("primary_key", False))
                        or column_name.casefold()
                        in {str(name).casefold() for name in table_primary_keys}
                    ),
                    foreign_key=(
                        str(foreign_key)
                        if foreign_key
                        else table_foreign_keys.get(column_name.casefold())
                    ),
                )
            )
        tables.append(SchemaTable(name=name, columns=tuple(columns)))

    if not tables:
        raise SchemaParseError("The JSON schema does not define any tables.")
    return DatabaseSchema(
        context=_format_schema_context(text, tables),
        tables=tuple(tables),
    )


def _json_foreign_keys(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(column).casefold(): str(reference)
            for column, reference in value.items()
        }
    if not isinstance(value, list):
        return {}

    references: dict[str, str] = {}
    for foreign_key in value:
        if not isinstance(foreign_key, dict):
            continue
        columns = foreign_key.get("column") or foreign_key.get("columns")
        if isinstance(columns, str):
            columns = [columns]
        if not isinstance(columns, list):
            continue
        target = (
            foreign_key.get("references")
            or foreign_key.get("ref")
            or foreign_key.get("target")
        )
        if isinstance(target, dict):
            target = ".".join(
                str(part)
                for part in (target.get("table"), target.get("column"))
                if part
            )
        if target:
            references.update(
                {str(column).casefold(): str(target) for column in columns}
            )
    return references


def _parse_text_schema(text: str) -> DatabaseSchema:
    table_pattern = re.compile(
        r"^\s*(?:table\s+|#{1,6}\s*table\s+)([A-Za-z_][\w.]*)\s*:?\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    column_pattern = re.compile(
        r"^\s*(?:[-*]\s*)?([A-Za-z_]\w*)\s*:\s*"
        r"([A-Za-z][\w]*(?:\([^)]*\))?)(.*)$",
        flags=re.IGNORECASE,
    )
    matches = list(table_pattern.finditer(text))
    tables: list[SchemaTable] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        columns: list[SchemaColumn] = []
        for line in text[body_start:body_end].splitlines():
            column_match = column_pattern.match(line)
            if not column_match:
                continue
            name, data_type, constraints = column_match.groups()
            reference = re.search(
                r"\bREFERENCES\s+([A-Za-z_][\w.]*)",
                constraints,
                flags=re.IGNORECASE,
            )
            columns.append(
                SchemaColumn(
                    name=name,
                    data_type=data_type,
                    primary_key=bool(
                        re.search(r"\bPRIMARY\s+KEY\b", constraints, re.IGNORECASE)
                    ),
                    foreign_key=reference.group(1) if reference else None,
                )
            )
        tables.append(SchemaTable(name=match.group(1), columns=tuple(columns)))

    return DatabaseSchema(
        context=_format_schema_context(text, tables) if tables else text.strip(),
        tables=tuple(tables),
    )


def _format_schema_context(
    original_schema: str,
    tables: list[SchemaTable],
) -> str:
    summaries = []
    for table in tables:
        columns = []
        for column in table.columns:
            details = [column.data_type]
            if column.primary_key:
                details.append("PRIMARY KEY")
            if column.foreign_key:
                details.append(f"REFERENCES {column.foreign_key}")
            columns.append(f"  - {column.name}: {' '.join(details)}")
        summaries.append(f"Table {table.name}:\n" + "\n".join(columns))
    summary_text = "\n\n".join(summaries)
    return (
        f"Original schema:\n{original_schema.strip()}\n\n"
        f"Parsed schema metadata:\n{summary_text}"
    )


def _expression_name(expression: exp.Expression) -> str:
    if isinstance(expression, exp.Table):
        return expression.name
    return expression.name if hasattr(expression, "name") else ""


def _unquote_identifier(value: str) -> str:
    return value.strip().strip('"`[] ')
