import unittest
from unittest.mock import Mock

from comparator.data_integrity_checker import DataIntegrityChecker
from db.connection import DatabaseConnection
from db.metadata import MetadataReader
from db.table_data_cleaner import TableDataCleaner


class FakeManagedConnection:
    parsed_url = {
        "engine": "postgresql",
        "database": "test_database",
    }
    config = {"schema": "public"}

    def __init__(self) -> None:
        self.connection = Mock()
        self.close_calls = 0

    def connect(self):
        return self.connection

    def close(self) -> None:
        self.close_calls += 1


class ConnectionLifecycleTests(unittest.TestCase):
    def test_database_connection_forgets_driver_connection_even_if_close_fails(self) -> None:
        db = DatabaseConnection(
            {
                "url": "jdbc:postgresql://localhost/test_database",
                "user": "user",
                "password": "password",
            }
        )
        driver_connection = Mock()
        driver_connection.close.side_effect = RuntimeError("close failed")
        db.connection = driver_connection

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            db.close()

        self.assertIsNone(db.connection)

    def test_metadata_reader_closes_connection_on_error(self) -> None:
        db = FakeManagedConnection()
        reader = MetadataReader(db)
        reader._read_postgresql = Mock(side_effect=RuntimeError("read failed"))

        with self.assertRaisesRegex(RuntimeError, "read failed"):
            reader.read()

        self.assertEqual(db.close_calls, 1)

    def test_integrity_checker_closes_connection_on_error(self) -> None:
        db = FakeManagedConnection()
        checker = DataIntegrityChecker(db, {"columns": {}}, {"tables": {}})
        checker._check_required_nulls = Mock(side_effect=RuntimeError("check failed"))

        with self.assertRaisesRegex(RuntimeError, "check failed"):
            checker.check()

        self.assertEqual(db.close_calls, 1)

    def test_table_cleaner_closes_connection_when_plan_fails(self) -> None:
        db = FakeManagedConnection()
        cleaner = TableDataCleaner(db)
        cleaner._find_target_table = Mock(side_effect=RuntimeError("plan failed"))

        with self.assertRaisesRegex(RuntimeError, "plan failed"):
            cleaner.build_plan("orders")

        self.assertEqual(db.close_calls, 1)

    def test_table_cleaner_closes_connection_when_execute_cannot_connect(self) -> None:
        db = FakeManagedConnection()
        db.connect = Mock(side_effect=RuntimeError("connect failed"))
        cleaner = TableDataCleaner(db)
        plan = Mock(engine="postgresql", database="test_database")

        with self.assertRaisesRegex(RuntimeError, "connect failed"):
            cleaner.execute(plan)

        self.assertEqual(db.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
