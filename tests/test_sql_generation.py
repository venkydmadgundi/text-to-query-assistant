from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.runnables import RunnableLambda

from services.sql_generator import SchemaSQLGenerator


class SchemaSQLGenerationTests(unittest.TestCase):
    def test_generate_sql_only_never_validates_or_executes(self) -> None:
        generator = SchemaSQLGenerator(
            RunnableLambda(lambda _prompt: "SELECT id FROM employees;")
        )
        with (
            patch(
                "tools.sql_tools.validate_read_only_sql",
                side_effect=AssertionError("Schema mode must not validate SQL."),
            ) as validate,
            patch(
                "tools.sql_tools.execute_read_only_query",
                side_effect=AssertionError("Schema mode must not execute SQL."),
            ) as execute,
        ):
            result = generator.generate_sql_only(
                "CREATE TABLE employees (id INTEGER);",
                "List employee ids",
            )

        self.assertEqual(result, "SELECT id FROM employees;")
        validate.assert_not_called()
        execute.assert_not_called()

    def test_generate_sql_only_removes_model_markdown_fences(self) -> None:
        generator = SchemaSQLGenerator(
            RunnableLambda(lambda _prompt: "```sql\nSELECT id FROM employees;\n```")
        )

        result = generator.generate_sql_only(
            "CREATE TABLE employees (id INTEGER);",
            "List employee ids",
        )

        self.assertEqual(result, "SELECT id FROM employees;")


if __name__ == "__main__":
    unittest.main()
