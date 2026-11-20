from __future__ import annotations

import unittest

from services.schema_parser import SchemaParseError, parse_schema_file


class SchemaParserTests(unittest.TestCase):
    def test_sql_schema_extracts_tables_columns_and_keys(self) -> None:
        schema = parse_schema_file(
            "company.sql",
            b"""
            CREATE TABLE departments (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY,
                department_id INTEGER,
                FOREIGN KEY (department_id) REFERENCES departments(id)
            );
            """,
        )

        self.assertEqual(
            [table.name for table in schema.tables],
            ["departments", "employees"],
        )
        employee_columns = {
            column.name: column for column in schema.tables[1].columns
        }
        self.assertEqual(employee_columns["department_id"].data_type, "INT")
        self.assertTrue(employee_columns["id"].primary_key)
        self.assertEqual(
            employee_columns["department_id"].foreign_key,
            "departments.id",
        )
        self.assertIn("FOREIGN KEY", schema.context)

    def test_json_schema_accepts_table_arrays_and_foreign_key_metadata(self) -> None:
        schema = parse_schema_file(
            "schema.json",
            b"""{
              "tables": [
                {
                  "name": "employees",
                  "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "department_id", "type": "INTEGER"}
                  ],
                  "primary_key": ["id"],
                  "foreign_keys": [
                    {
                      "column": "department_id",
                      "references": "departments.id"
                    }
                  ]
                }
              ]
            }""",
        )

        self.assertEqual(schema.tables[0].name, "employees")
        self.assertTrue(schema.tables[0].columns[0].primary_key)
        self.assertEqual(
            schema.tables[0].columns[1].foreign_key,
            "departments.id",
        )

    def test_json_schema_accepts_table_and_column_mappings(self) -> None:
        schema = parse_schema_file(
            "schema.json",
            b'{"tables":{"employees":{"columns":{"id":{"type":"INTEGER"}}}}}',
        )

        self.assertEqual(schema.tables[0].columns[0].name, "id")
        self.assertIn("Table employees", schema.context)

    def test_text_schema_extracts_columns_and_relationships(self) -> None:
        schema = parse_schema_file(
            "schema.txt",
            b"""Table employees:
- id: INTEGER PRIMARY KEY
- department_id: INTEGER REFERENCES departments.id
""",
        )

        self.assertEqual(schema.tables[0].name, "employees")
        self.assertTrue(schema.tables[0].columns[0].primary_key)
        self.assertEqual(
            schema.tables[0].columns[1].foreign_key,
            "departments.id",
        )

    def test_text_schema_keeps_unstructured_description_as_context(self) -> None:
        schema = parse_schema_file(
            "schema.txt",
            b"Employee data includes names, departments, and salaries.",
        )

        self.assertEqual(schema.tables, ())
        self.assertIn("departments", schema.context)

    def test_unsupported_or_non_create_sql_is_rejected(self) -> None:
        with self.assertRaises(SchemaParseError):
            parse_schema_file("schema.pdf", b"")
        with self.assertRaises(SchemaParseError):
            parse_schema_file("schema.sql", b"SELECT 1;")


if __name__ == "__main__":
    unittest.main()
