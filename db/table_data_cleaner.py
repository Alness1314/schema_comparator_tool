from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from db.connection import DatabaseConnection


@dataclass(frozen=True, order=True)
class TableRef:
    schema: str
    name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class DeletionPlan:
    engine: str
    database: str
    target: TableRef
    deletion_order: List[TableRef]
    row_counts: Dict[TableRef, int]
    generated_at: datetime

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())


class TableDataCleaner:
    """Analiza y vacia una tabla junto con sus tablas dependientes."""

    def __init__(self, db_connection: DatabaseConnection) -> None:
        self.db_connection = db_connection
        self.engine = db_connection.parsed_url["engine"]
        self.database = db_connection.parsed_url["database"]

    def build_plan(self, table_name: str) -> DeletionPlan:
        try:
            clean_name = table_name.strip()
            if not clean_name:
                raise ValueError("Escribe el nombre de la tabla.")

            connection = self.db_connection.connect()
            target = self._find_target_table(connection, clean_name)
            dependencies = self._read_dependencies(connection)
            deletion_order = self._resolve_deletion_order(target, dependencies)
            row_counts = {
                table: self._count_rows(connection, table)
                for table in deletion_order
            }

            return DeletionPlan(
                engine=self.engine,
                database=self.database,
                target=target,
                deletion_order=deletion_order,
                row_counts=row_counts,
                generated_at=datetime.now(),
            )
        finally:
            self.close()

    def execute(self, plan: DeletionPlan) -> Dict[TableRef, int]:
        if plan.engine != self.engine or plan.database != self.database:
            raise ValueError("El plan no pertenece a esta conexion.")

        connection = None
        deleted_rows: Dict[TableRef, int] = {}
        try:
            connection = self.db_connection.connect()
            for table in plan.deletion_order:
                before_count = self._count_rows(connection, table)
                cursor = connection.cursor()
                try:
                    cursor.execute(f"DELETE FROM {self._qualified_identifier(table)}")
                    affected = cursor.rowcount
                finally:
                    cursor.close()

                deleted_rows[table] = before_count if affected is None or affected < 0 else affected

            connection.commit()
            return deleted_rows
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            self.close()

    def close(self) -> None:
        self.db_connection.close()

    def _find_target_table(self, connection: Any, table_name: str) -> TableRef:
        configured_schema = self.db_connection.config.get("schema", "").strip()
        schema = configured_schema or self._default_schema()

        queries = {
            "postgresql": """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema = %s
                  AND lower(table_name) = lower(%s)
                ORDER BY CASE WHEN table_name = %s THEN 0 ELSE 1 END;
            """,
            "mysql": """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema = %s
                  AND lower(table_name) = lower(%s)
                ORDER BY CASE WHEN table_name = %s THEN 0 ELSE 1 END;
            """,
            "sqlserver": """
                SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE'
                  AND TABLE_SCHEMA = ?
                  AND lower(TABLE_NAME) = lower(?)
                ORDER BY CASE WHEN TABLE_NAME = ? THEN 0 ELSE 1 END;
            """,
        }
        rows = self._fetch_all(connection, queries[self.engine], (schema, table_name, table_name))
        if not rows:
            raise ValueError(
                f"No existe la tabla '{table_name}' en el esquema '{schema}'."
            )
        exact_matches = [row for row in rows if row["table_name"] == table_name]
        if len(exact_matches) == 1:
            selected = exact_matches[0]
        elif len(rows) == 1:
            selected = rows[0]
        else:
            raise ValueError(
                f"El nombre '{table_name}' coincide con varias tablas. Usa la capitalizacion exacta."
            )
        return TableRef(selected["table_schema"], selected["table_name"])

    def _read_dependencies(self, connection: Any) -> Dict[TableRef, Set[TableRef]]:
        queries = {
            "postgresql": """
                SELECT
                    child_ns.nspname AS child_schema,
                    child.relname AS child_table,
                    parent_ns.nspname AS parent_schema,
                    parent.relname AS parent_table
                FROM pg_constraint fk
                JOIN pg_class child ON child.oid = fk.conrelid
                JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
                JOIN pg_class parent ON parent.oid = fk.confrelid
                JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
                WHERE fk.contype = 'f';
            """,
            "mysql": """
                SELECT DISTINCT
                    TABLE_SCHEMA AS child_schema,
                    TABLE_NAME AS child_table,
                    REFERENCED_TABLE_SCHEMA AS parent_schema,
                    REFERENCED_TABLE_NAME AS parent_table
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE REFERENCED_TABLE_NAME IS NOT NULL;
            """,
            "sqlserver": """
                SELECT
                    child_schema.name AS child_schema,
                    child.name AS child_table,
                    parent_schema.name AS parent_schema,
                    parent.name AS parent_table
                FROM sys.foreign_keys fk
                JOIN sys.tables child ON child.object_id = fk.parent_object_id
                JOIN sys.schemas child_schema ON child_schema.schema_id = child.schema_id
                JOIN sys.tables parent ON parent.object_id = fk.referenced_object_id
                JOIN sys.schemas parent_schema ON parent_schema.schema_id = parent.schema_id;
            """,
        }
        dependencies: Dict[TableRef, Set[TableRef]] = {}
        for row in self._fetch_all(connection, queries[self.engine], ()):
            parent = TableRef(row["parent_schema"], row["parent_table"])
            child = TableRef(row["child_schema"], row["child_table"])
            if child != parent:
                dependencies.setdefault(parent, set()).add(child)
        return dependencies

    def _resolve_deletion_order(
        self,
        target: TableRef,
        dependencies: Dict[TableRef, Set[TableRef]],
    ) -> List[TableRef]:
        order: List[TableRef] = []
        visited: Set[TableRef] = set()
        active: List[TableRef] = []

        def visit(table: TableRef) -> None:
            if table in active:
                cycle_start = active.index(table)
                cycle = active[cycle_start:] + [table]
                cycle_text = " -> ".join(item.qualified_name for item in cycle)
                raise ValueError(
                    "No se puede garantizar un borrado seguro porque hay un ciclo de "
                    f"claves foraneas: {cycle_text}."
                )
            if table in visited:
                return

            active.append(table)
            for child in sorted(dependencies.get(table, set())):
                visit(child)
            active.pop()
            visited.add(table)
            order.append(table)

        visit(target)
        return order

    def _count_rows(self, connection: Any, table: TableRef) -> int:
        cursor = connection.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) AS row_count FROM {self._qualified_identifier(table)}")
            row = cursor.fetchone()
            if isinstance(row, dict):
                return int(row["row_count"])
            return int(row[0])
        finally:
            cursor.close()

    def _qualified_identifier(self, table: TableRef) -> str:
        return f"{self._quote_identifier(table.schema)}.{self._quote_identifier(table.name)}"

    def _quote_identifier(self, value: str) -> str:
        if self.engine == "mysql":
            return f"`{value.replace('`', '``')}`"
        if self.engine == "sqlserver":
            return f"[{value.replace(']', ']]')}]"
        return f'"{value.replace(chr(34), chr(34) * 2)}"'

    def _default_schema(self) -> str:
        return {
            "postgresql": "public",
            "mysql": self.database,
            "sqlserver": "dbo",
        }[self.engine]

    def _fetch_all(
        self,
        connection: Any,
        query: str,
        params: Tuple[Any, ...],
    ) -> List[Dict[str, Any]]:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
            return [
                dict(row) if isinstance(row, dict) else dict(zip(columns, row))
                for row in rows
            ]
        finally:
            cursor.close()
