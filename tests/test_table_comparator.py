from datetime import datetime
import tempfile
import unittest

from comparator.table_comparator import TableComparator, TableComparisonResult
from generator.table_comparison_report import (
    table_comparison_lines,
    write_table_comparison_report,
)
from comparator.table_comparator import TableSyncResult
from generator.table_sync_report import table_sync_lines, write_table_sync_report


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.index = 0
        self.closed = False

    def execute(self, _query):
        return None

    def fetchmany(self, size):
        batch = self.rows[self.index : self.index + size]
        self.index += size
        return batch

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


class FakeDatabaseConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def connect(self):
        return FakeConnection(self.rows)

    def close(self):
        self.closed = True


class TableComparatorTests(unittest.TestCase):
    def test_compare_data_detects_source_and_target_only_rows(self) -> None:
        comparator = TableComparator.__new__(TableComparator)
        comparator.source_connection = FakeDatabaseConnection(
            [{"id": 1, "name": "same"}, {"id": 2, "name": "source"}]
        )
        comparator.target_connection = FakeDatabaseConnection(
            [{"id": 1, "name": "same"}, {"id": 3, "name": "target"}]
        )
        comparator.sample_size = 20
        metadata = {
            "schema": "public",
            "columns": {
                "orders": {
                    "id": {"ordinal_position": 1},
                    "name": {"ordinal_position": 2},
                }
            },
        }

        result = comparator._compare_data(metadata, metadata, "orders", "orders")

        self.assertTrue(result["data_compared"])
        self.assertFalse(result["data_equal"])
        self.assertEqual(result["source_row_count"], 2)
        self.assertEqual(result["target_row_count"], 2)
        self.assertEqual(result["only_source_count"], 1)
        self.assertEqual(result["only_target_count"], 1)
        self.assertIn((2, "source"), result["only_source_samples"])
        self.assertIn((3, "target"), result["only_target_samples"])


class TableComparisonReportTests(unittest.TestCase):
    def test_report_marks_table_as_different(self) -> None:
        result = TableComparisonResult(
            table_name="orders",
            source_name="ORIGEN",
            target_name="DESTINO",
            source_schema="public",
            target_schema="public",
            source_table_name="orders",
            target_table_name="orders",
            source_schema_sql='CREATE TABLE "public"."orders" ();',
            target_schema_sql='CREATE TABLE "public"."orders" ();',
            generated_at=datetime(2026, 6, 17, 12, 0),
            structure_differences=[
                {
                    "type": "COLUMN_DIFFERENT",
                    "impact": "ALTO",
                    "description": "La columna 'name' tiene definicion distinta.",
                }
            ],
            source_row_count=2,
            target_row_count=2,
            data_compared=True,
            data_equal=False,
            data_skip_reason="",
            only_source_count=1,
            only_target_count=1,
            only_source_samples=[(2, "source")],
            only_target_samples=[(3, "target")],
            compared_columns=["id", "name"],
            schema_sync_sql='ALTER TABLE "public"."orders" ADD COLUMN "name" TEXT;',
        )

        lines = table_comparison_lines(result)

        self.assertIn("Estado general: DIFERENTE", lines)
        self.assertIn("Datos: DIFERENTES", lines)
        self.assertIn("Comparando: ORIGEN -> DESTINO", lines)

        with tempfile.TemporaryDirectory() as temp_dir:
            txt_path, pdf_path = write_table_comparison_report(result, temp_dir)

            self.assertTrue(txt_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertIn(
                "REPORTE DE COMPARACION DE TABLA",
                txt_path.read_text(encoding="utf-8"),
            )


class TableSyncReportTests(unittest.TestCase):
    def test_sync_report_contains_type_and_direction(self) -> None:
        result = TableSyncResult(
            table_name="orders",
            source_name="ORIGEN",
            target_name="DESTINO",
            sync_type="DATA",
            generated_at=datetime(2026, 6, 17, 12, 30),
            statements_executed=3,
            rows_copied=2,
            status="COMPLETADO",
            detail="Data igualada correctamente.",
        )

        lines = table_sync_lines(result)

        self.assertIn("Tipo: DATA", lines)
        self.assertIn("Direccion: ORIGEN -> DESTINO", lines)

        with tempfile.TemporaryDirectory() as temp_dir:
            txt_path, pdf_path = write_table_sync_report(result, temp_dir)

            self.assertTrue(txt_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertIn(
                "REPORTE DE IGUALACION DE TABLA",
                txt_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
