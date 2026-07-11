from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock

from db.table_data_cleaner import TableRef
from db.table_dropper import DropTablesPlan, RelatedTablesDropper
from generator.table_drop_report import (
    drop_tables_plan_lines,
    write_drop_tables_report,
)


class TableDropperTests(unittest.TestCase):
    def test_execute_drops_tables_in_plan_order(self) -> None:
        connection = Mock()
        cursors = [Mock(), Mock()]
        connection.cursor.side_effect = cursors
        db = Mock()
        db.parsed_url = {"engine": "postgresql", "database": "test_database"}
        db.connect.return_value = connection
        dropper = RelatedTablesDropper(db)
        child = TableRef("public", "child")
        parent = TableRef("public", "parent")
        plan = DropTablesPlan(
            engine="postgresql",
            database="test_database",
            target=parent,
            drop_order=[child, parent],
            row_counts={child: 2, parent: 1},
            generated_at=datetime(2026, 6, 17, 12, 0),
        )

        dropped = dropper.execute(plan)

        self.assertEqual(dropped, [child, parent])
        cursors[0].execute.assert_called_once_with('DROP TABLE "public"."child"')
        cursors[1].execute.assert_called_once_with('DROP TABLE "public"."parent"')
        connection.commit.assert_called_once()
        db.close.assert_called()


class TableDropReportTests(unittest.TestCase):
    def test_drop_report_contains_preview_and_final_details(self) -> None:
        child = TableRef("public", "child")
        parent = TableRef("public", "parent")
        plan = DropTablesPlan(
            engine="postgresql",
            database="test_database",
            target=parent,
            drop_order=[child, parent],
            row_counts={child: 2, parent: 1},
            generated_at=datetime(2026, 6, 17, 12, 0),
        )

        preview = drop_tables_plan_lines(plan, "BD DESTINO")
        self.assertIn("Tablas que se eliminaran: 2", preview)

        with tempfile.TemporaryDirectory() as temp_dir:
            txt_path, pdf_path = write_drop_tables_report(
                plan,
                [child, parent],
                "BD DESTINO",
                temp_dir,
            )

            self.assertTrue(txt_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertIn(
                "REPORTE FINAL DE ELIMINACION DE TABLAS",
                txt_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
