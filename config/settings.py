from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "employees.db"
DEFAULT_CHROMA_DIRECTORY = PROJECT_ROOT / "chroma_db"


class SettingsError(ValueError):
    """Raised when an application setting is invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    max_result_rows: int = 500
    chroma_persist_directory: Path = DEFAULT_CHROMA_DIRECTORY


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        database_url = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"

    max_rows_value = os.getenv("MAX_RESULT_ROWS", "500")
    try:
        max_result_rows = int(max_rows_value)
    except ValueError as exc:
        raise SettingsError("MAX_RESULT_ROWS must be a whole number.") from exc
    if not 1 <= max_result_rows <= 5000:
        raise SettingsError("MAX_RESULT_ROWS must be between 1 and 5000.")

    chroma_directory_value = os.getenv("CHROMA_PERSIST_DIRECTORY")
    chroma_persist_directory = (
        Path(chroma_directory_value).expanduser()
        if chroma_directory_value
        else DEFAULT_CHROMA_DIRECTORY
    )
    if not chroma_persist_directory.is_absolute():
        chroma_persist_directory = PROJECT_ROOT / chroma_persist_directory

    return Settings(
        database_url=database_url,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        max_result_rows=max_result_rows,
        chroma_persist_directory=chroma_persist_directory,
    )
