from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, Date, ForeignKey, Integer, String, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.engine import Engine


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    designation: Mapped[str] = mapped_column(String(100), nullable=False)
    salary: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    joining_date: Mapped[date] = mapped_column(Date, nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"),
        nullable=True,
        index=True,
    )


def get_table_names(engine: Engine) -> list[str]:
    return inspect(engine).get_table_names()


def get_employee_schema(engine: Engine) -> str:
    columns = inspect(engine).get_columns("employees")
    return "\n".join(
        f"- {column['name']} ({column['type']})"
        + (" PRIMARY KEY" if column.get("primary_key") else "")
        + (" NOT NULL" if not column.get("nullable", True) else "")
        + (" REFERENCES employees.id" if column["name"] == "manager_id" else "")
        for column in columns
    )


def get_employee_schema_rows(engine: Engine) -> list[dict[str, Any]]:
    columns = inspect(engine).get_columns("employees")
    return [
        {
            "Column": column["name"],
            "Type": str(column["type"]),
            "Nullable": "Yes" if column.get("nullable", True) else "No",
            "Primary key": "Yes" if column.get("primary_key") else "No",
        }
        for column in columns
    ]
