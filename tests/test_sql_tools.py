from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect, text
from langchain_core.runnables import RunnableLambda

from agents.sql_agent import SQLAgentError, TextToSQLAgent
from database.connection import create_database_engine, initialize_database
from tools.sql_tools import (
    SQLExecutionError,
    SQLValidationError,
    _sqlite_read_only_authorizer,
    execute_read_only_query,
    validate_read_only_sql,
)


class SQLToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "employees.db"
        self.engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
        initialize_database(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_directory.cleanup()

    def test_seed_is_idempotent_and_includes_manager_relationships(self) -> None:
        self.assertEqual(initialize_database(self.engine), 0)
        with self.engine.connect() as connection:
            employee_count = connection.scalar(text("SELECT COUNT(*) FROM employees"))
            manager_count = connection.scalar(
                text("SELECT COUNT(*) FROM employees WHERE manager_id IS NOT NULL")
            )
        self.assertEqual(employee_count, 28)
        self.assertGreater(manager_count, 0)

    def test_employee_schema_matches_the_requested_columns(self) -> None:
        columns = inspect(self.engine).get_columns("employees")
        self.assertEqual(
            [column["name"] for column in columns],
            [
                "id",
                "first_name",
                "last_name",
                "email",
                "department",
                "designation",
                "salary",
                "joining_date",
                "city",
                "manager_id",
            ],
        )
        self.assertIn("DECIMAL", str(columns[6]["type"]).upper())

    def test_valid_select_and_cte_are_allowed(self) -> None:
        validate_read_only_sql("SELECT first_name FROM employees")
        validate_read_only_sql("SELECT first_name FROM employees;")
        validate_read_only_sql("SELECT first_name FROM employees; -- generated terminator")
        validate_read_only_sql(
            "WITH department_counts AS "
            "(SELECT department, COUNT(*) AS total FROM employees GROUP BY department) "
            "SELECT * FROM department_counts"
        )

    def test_mutations_and_multiple_statements_are_rejected(self) -> None:
        for sql in (
            "DELETE FROM employees",
            "SELECT * FROM employees; DROP TABLE employees",
            "WITH changed AS (DELETE FROM employees) SELECT 1",
            "SELECT 1; SELECT 2",
            "SELECT 1;;",
            "; SELECT 1",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLValidationError):
                validate_read_only_sql(sql)

    def test_sql_literals_comments_and_identifiers_do_not_trigger_keyword_checks(self) -> None:
        validate_read_only_sql(
            "SELECT 'delete; update' AS note FROM employees "
            "/* DROP TABLE employees */ WHERE department = 'HR'"
        )

    def test_query_results_are_limited_and_system_tables_are_not_readable(self) -> None:
        count_result = execute_read_only_query(
            self.engine,
            "SELECT COUNT(*) AS employee_count FROM employees;",
        )
        self.assertEqual(count_result.rows, [{"employee_count": 28}])

        result = execute_read_only_query(
            self.engine,
            "SELECT id, first_name FROM employees ORDER BY id;",
            max_rows=3,
        )
        self.assertEqual(result.row_count, 3)
        self.assertTrue(result.truncated)
        self.assertEqual(result.columns, ["id", "first_name"])

        with self.assertRaises(SQLExecutionError):
            execute_read_only_query(
                self.engine,
                "SELECT name FROM sqlite_master",
            )

    def test_sqlite_authorizer_blocks_writes_even_beyond_validation(self) -> None:
        raw_connection = self.engine.raw_connection()
        driver_connection = raw_connection.driver_connection
        driver_connection.set_authorizer(_sqlite_read_only_authorizer)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                driver_connection.execute(
                    "UPDATE employees SET salary = 0 WHERE id = 1"
                )
        finally:
            driver_connection.set_authorizer(None)
            raw_connection.close()

    def test_agent_generates_explains_and_executes_a_guarded_query(self) -> None:
        def fixed_query(prompt: object) -> str:
            if prompt is None:
                raise AssertionError("The LangChain prompt should not be empty.")
            return (
                "SELECT department, COUNT(*) AS total FROM employees "
                "GROUP BY department ORDER BY total DESC;"
            )

        agent = TextToSQLAgent(
            llm=RunnableLambda(fixed_query),
            engine=self.engine,
            schema="employees(id, department)",
        )

        answer = agent.ask("Count employees in each department.")

        self.assertIn("SELECT department", answer.sql)
        self.assertIn("calculates a count", answer.explanation)
        self.assertGreater(answer.result.row_count, 0)

    def test_agent_does_not_execute_generated_mutations(self) -> None:
        def fixed_query(prompt: object) -> str:
            if prompt is None:
                raise AssertionError("The LangChain prompt should not be empty.")
            return "DROP TABLE employees"

        agent = TextToSQLAgent(
            llm=RunnableLambda(fixed_query),
            engine=self.engine,
            schema="employees(id)",
        )

        with self.assertRaises(SQLAgentError) as context:
            agent.ask("Drop the employee table.")

        self.assertIn("DROP TABLE", context.exception.generated_sql or "")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT COUNT(*) FROM employees")),
                28,
            )


if __name__ == "__main__":
    unittest.main()
