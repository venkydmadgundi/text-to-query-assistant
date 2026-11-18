from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from agents.sql_agent import TextToSQLAgent, create_text_to_sql_agent
from config.settings import Settings
from database.connection import create_database_engine, initialize_database
from database.schema import get_employee_schema_rows, get_table_names
from utils.logger import get_logger


logger = get_logger(__name__)


class ApplicationInitializationError(RuntimeError):
    """Raised when the database-backed application cannot start."""


@dataclass(frozen=True)
class ApplicationServices:
    agent: TextToSQLAgent | None
    database_label: str
    table_names: list[str]
    employee_schema: list[dict[str, str]]


def create_application_services(settings: Settings) -> ApplicationServices:
    try:
        engine = create_database_engine(settings.database_url)
        initialize_database(engine)
        table_names = get_table_names(engine)
        employee_schema = get_employee_schema_rows(engine)
    except (ImportError, OSError, SQLAlchemyError, ValueError) as exc:
        logger.error("Database initialization failed (%s).", type(exc).__name__)
        raise ApplicationInitializationError(
            "The employee database could not be initialized. Check DATABASE_URL "
            "and the database permissions."
        ) from exc

    try:
        agent = (
            create_text_to_sql_agent(settings, engine)
            if settings.groq_api_key
            else None
        )
    except (TypeError, ValueError) as exc:
        logger.error("LLM initialization failed (%s).", type(exc).__name__)
        raise ApplicationInitializationError(
            "The Groq client could not be initialized. Check GROQ_API_KEY and GROQ_MODEL."
        ) from exc

    url = make_url(settings.database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        database_label = (
            f"SQLite ({Path(url.database).name})" if url.database else "SQLite"
        )
    else:
        database_label = backend

    return ApplicationServices(
        agent=agent,
        database_label=database_label,
        table_names=table_names,
        employee_schema=employee_schema,
    )
