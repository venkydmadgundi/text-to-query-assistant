from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from database.schema import Base


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, object] = {}

    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:" and not url.database.startswith("file:"):
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            connection: sqlite3.Connection,
            _record: object,
        ) -> None:
            _ = _record
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def initialize_database(engine: Engine) -> int:
    Base.metadata.create_all(engine)

    from database.seed import seed_employees

    return seed_employees(engine)
