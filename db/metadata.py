from typing import Any, Dict, List

from db.connection import DatabaseConnection


class MetadataReader:
    """Lee metadata estructural de una base de datos."""

    def __init__(self, db_connection: DatabaseConnection) -> None:
        self.db_connection = db_connection

    def read(self) -> Dict[str, Any]:
        engine = self.db_connection.parsed_url["engine"]
        if engine != "postgresql":
            raise NotImplementedError(
                "La lectura completa de metadata esta implementada por ahora para PostgreSQL."
            )

        return self._read_postgresql()

    def _read_postgresql(self) -> Dict[str, Any]:
        schema = self.db_connection.config.get("schema") or "public"
        connection = self.db_connection.connect()

        return {
            "engine": "postgresql",
            "schema": schema,
            "schemas": self._fetch_all(
                connection,
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name = %s
                ORDER BY schema_name;
                """,
                (schema,),
            ),
            "tables": self._read_tables(connection, schema),
            "columns": self._read_columns(connection, schema),
            "constraints": self._read_constraints(connection, schema),
            "indexes": self._read_indexes(connection, schema),
            "views": self._read_views(connection, schema),
            "sequences": self._read_sequences(connection, schema),
        }

    def _read_tables(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
            """,
            (schema,),
        )
        return {row["table_name"]: row for row in rows}

    def _read_columns(self, connection: Any, schema: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                table_name,
                column_name,
                ordinal_position,
                column_default,
                is_nullable,
                data_type,
                udt_name,
                character_maximum_length,
                numeric_precision,
                numeric_scale,
                datetime_precision
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position;
            """,
            (schema,),
        )
        columns: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in rows:
            columns.setdefault(row["table_name"], {})[row["column_name"]] = row
        return columns

    def _read_constraints(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                con.conname AS constraint_name,
                cls.relname AS table_name,
                con.contype AS constraint_type,
                pg_get_constraintdef(con.oid, true) AS definition
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
            WHERE nsp.nspname = %s
            ORDER BY cls.relname, con.conname;
            """,
            (schema,),
        )
        return {self._object_key(row["table_name"], row["constraint_name"]): row for row in rows}

    def _read_indexes(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                schemaname AS schema_name,
                tablename AS table_name,
                indexname AS index_name,
                indexdef
            FROM pg_indexes
            WHERE schemaname = %s
            ORDER BY tablename, indexname;
            """,
            (schema,),
        )
        return {self._object_key(row["table_name"], row["index_name"]): row for row in rows}

    def _read_views(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT table_name AS view_name, view_definition
            FROM information_schema.views
            WHERE table_schema = %s
            ORDER BY table_name;
            """,
            (schema,),
        )
        return {row["view_name"]: row for row in rows}

    def _read_sequences(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT sequence_name, data_type, start_value, minimum_value, maximum_value, increment
            FROM information_schema.sequences
            WHERE sequence_schema = %s
            ORDER BY sequence_name;
            """,
            (schema,),
        )
        return {row["sequence_name"]: row for row in rows}

    def _fetch_all(self, connection: Any, query: str, params: tuple[Any, ...]) -> List[Dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def _object_key(self, table_name: str, object_name: str) -> str:
        return f"{table_name}.{object_name}"
