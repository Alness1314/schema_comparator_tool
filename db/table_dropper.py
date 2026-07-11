from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from db.connection import DatabaseConnection
from db.table_data_cleaner import TableDataCleaner, TableRef


@dataclass
class DropTablesPlan:
    engine: str
    database: str
    target: TableRef
    drop_order: List[TableRef]
    row_counts: Dict[TableRef, int]
    generated_at: datetime

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())


class RelatedTablesDropper:
    """Analiza y elimina una tabla junto con tablas dependientes."""

    def __init__(self, db_connection: DatabaseConnection) -> None:
        self.db_connection = db_connection
        self.engine = db_connection.parsed_url["engine"]
        self.database = db_connection.parsed_url["database"]

    def build_plan(self, table_name: str) -> DropTablesPlan:
        cleaner = TableDataCleaner(self.db_connection)
        deletion_plan = cleaner.build_plan(table_name)
        return DropTablesPlan(
            engine=deletion_plan.engine,
            database=deletion_plan.database,
            target=deletion_plan.target,
            drop_order=deletion_plan.deletion_order,
            row_counts=deletion_plan.row_counts,
            generated_at=datetime.now(),
        )

    def execute(self, plan: DropTablesPlan) -> List[TableRef]:
        if plan.engine != self.engine or plan.database != self.database:
            raise ValueError("El plan no pertenece a esta conexion.")

        connection = None
        cleaner = TableDataCleaner(self.db_connection)
        dropped_tables: List[TableRef] = []
        try:
            connection = self.db_connection.connect()
            for table in plan.drop_order:
                cursor = connection.cursor()
                try:
                    cursor.execute(f"DROP TABLE {cleaner._qualified_identifier(table)}")
                finally:
                    cursor.close()
                dropped_tables.append(table)

            connection.commit()
            return dropped_tables
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            self.close()

    def close(self) -> None:
        self.db_connection.close()
