from datetime import datetime
import tempfile
import unittest

from db.table_data_cleaner import DeletionPlan, TableDataCleaner, TableRef
from generator.deletion_report import deletion_plan_lines, write_deletion_report


class FakeDatabaseConnection:
    parsed_url = {
        "engine": "postgresql",
        "database": "test_database",
    }
    config = {"schema": "public"}


class TableDataCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cleaner = TableDataCleaner(FakeDatabaseConnection())

    def test_deletion_order_places_deepest_children_first(self) -> None:
        parent = TableRef("public", "orders")
        child = TableRef("public", "order_items")
        grandchild = TableRef("public", "item_notes")
        audit = TableRef("public", "order_audit")
        dependencies = {
            parent: {child, audit},
            child: {grandchild},
        }

        order = self.cleaner._resolve_deletion_order(parent, dependencies)

        self.assertLess(order.index(grandchild), order.index(child))
        self.assertLess(order.index(child), order.index(parent))
        self.assertLess(order.index(audit), order.index(parent))
        self.assertEqual(len(order), 4)

    def test_cycle_blocks_deletion_plan(self) -> None:
        first = TableRef("public", "first")
        second = TableRef("public", "second")
        dependencies = {
            first: {second},
            second: {first},
        }

        with self.assertRaisesRegex(ValueError, "ciclo de claves foraneas"):
            self.cleaner._resolve_deletion_order(first, dependencies)

    def test_identifiers_are_quoted(self) -> None:
        table = TableRef("odd schema", 'table"name')

        quoted = self.cleaner._qualified_identifier(table)

        self.assertEqual(quoted, '"odd schema"."table""name"')


class DeletionReportTests(unittest.TestCase):
    def test_report_contains_preview_and_final_totals(self) -> None:
        target = TableRef("public", "orders")
        child = TableRef("public", "order_items")
        plan = DeletionPlan(
            engine="postgresql",
            database="test_database",
            target=target,
            deletion_order=[child, target],
            row_counts={child: 4, target: 2},
            generated_at=datetime(2026, 6, 12, 10, 30),
        )

        preview = deletion_plan_lines(plan, "BD DESTINO")
        self.assertIn("Filas detectadas: 6", preview)

        with tempfile.TemporaryDirectory() as temp_dir:
            txt_path, pdf_path = write_deletion_report(
                plan,
                {child: 4, target: 2},
                "BD DESTINO",
                output_dir=temp_dir,
            )

            self.assertTrue(txt_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertIn(
                "Total de filas borradas: 6",
                txt_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
