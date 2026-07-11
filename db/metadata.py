from typing import Any, Dict, List

from db.connection import DatabaseConnection


class MetadataReader:
    """Lee metadata estructural de una base de datos."""

    def __init__(self, db_connection: DatabaseConnection) -> None:
        self.db_connection = db_connection

    def read(self) -> Dict[str, Any]:
        try:
            engine = self.db_connection.parsed_url["engine"]
            if engine != "postgresql":
                raise NotImplementedError(
                    "La lectura completa de metadata esta implementada por ahora para PostgreSQL."
                )

            return self._read_postgresql()
        finally:
            self.db_connection.close()

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
            "materialized_views": self._read_materialized_views(connection, schema),
            "sequences": self._read_sequences(connection, schema),
            "functions": self._read_functions(connection, schema),
            "extensions": self._read_extensions(connection),
            "triggers": self._read_triggers(connection, schema),
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
                cls.relname AS table_name,
                att.attname AS column_name,
                att.attnum AS ordinal_position,
                pg_get_expr(def.adbin, def.adrelid) AS column_default,
                CASE WHEN att.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
                COALESCE(info.data_type, format_type(att.atttypid, att.atttypmod)) AS data_type,
                typ.typname AS udt_name,
                info.character_maximum_length,
                info.numeric_precision,
                info.numeric_scale,
                info.datetime_precision,
                att.attidentity AS identity_kind,
                CASE
                    WHEN att.attidentity = 'a' THEN 'ALWAYS'
                    WHEN att.attidentity = 'd' THEN 'BY DEFAULT'
                    ELSE ''
                END AS identity_generation,
                att.attgenerated AS generated_kind,
                CASE
                    WHEN att.attgenerated = 's' THEN pg_get_expr(def.adbin, def.adrelid)
                    ELSE NULL
                END AS generation_expression,
                coll.collname AS collation_name,
                pg_catalog.col_description(att.attrelid, att.attnum) AS comment,
                format_type(att.atttypid, att.atttypmod) AS formatted_type
            FROM pg_attribute att
            JOIN pg_class cls ON cls.oid = att.attrelid
            JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
            JOIN pg_type typ ON typ.oid = att.atttypid
            LEFT JOIN pg_attrdef def ON def.adrelid = att.attrelid AND def.adnum = att.attnum
            LEFT JOIN pg_collation coll
              ON coll.oid = att.attcollation
             AND att.attcollation <> typ.typcollation
            LEFT JOIN information_schema.columns info
              ON info.table_schema = nsp.nspname
             AND info.table_name = cls.relname
             AND info.column_name = att.attname
            WHERE nsp.nspname = %s
              AND cls.relkind IN ('r', 'p')
              AND att.attnum > 0
              AND NOT att.attisdropped
            ORDER BY cls.relname, att.attnum;
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
                pg_get_constraintdef(con.oid, true) AS definition,
                con.condeferrable AS deferrable,
                con.condeferred AS initially_deferred,
                con.confupdtype AS on_update,
                con.confdeltype AS on_delete,
                ref_ns.nspname AS referenced_schema,
                COALESCE(
                    (
                        SELECT array_agg(att.attname ORDER BY keys.ordinality)
                        FROM unnest(con.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
                        JOIN pg_attribute att
                          ON att.attrelid = con.conrelid
                         AND att.attnum = keys.attnum
                    ),
                    ARRAY[]::text[]
                ) AS columns,
                ref_cls.relname AS referenced_table,
                COALESCE(
                    (
                        SELECT array_agg(att.attname ORDER BY keys.ordinality)
                        FROM unnest(con.confkey) WITH ORDINALITY AS keys(attnum, ordinality)
                        JOIN pg_attribute att
                          ON att.attrelid = con.confrelid
                         AND att.attnum = keys.attnum
                    ),
                    ARRAY[]::text[]
                ) AS referenced_columns
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
            LEFT JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
            LEFT JOIN pg_namespace ref_ns ON ref_ns.oid = ref_cls.relnamespace
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
                nsp.nspname AS schema_name,
                tbl.relname AS table_name,
                idx.relname AS index_name,
                pg_get_indexdef(idx.oid) AS indexdef,
                am.amname AS method,
                ind.indisunique AS is_unique,
                ind.indisprimary AS is_primary,
                pg_get_expr(ind.indpred, ind.indrelid) AS predicate,
                pg_get_expr(ind.indexprs, ind.indrelid) AS expressions,
                COALESCE(
                    (
                        SELECT array_agg(att.attname ORDER BY keys.ordinality)
                        FROM unnest(ind.indkey) WITH ORDINALITY AS keys(attnum, ordinality)
                        JOIN pg_attribute att
                          ON att.attrelid = tbl.oid
                         AND att.attnum = keys.attnum
                        WHERE keys.attnum > 0
                    ),
                    ARRAY[]::text[]
                ) AS columns,
                COALESCE(
                    (
                        SELECT array_agg(opc.opcname ORDER BY keys.ordinality)
                        FROM unnest(ind.indclass) WITH ORDINALITY AS keys(opcoid, ordinality)
                        JOIN pg_opclass opc ON opc.oid = keys.opcoid
                    ),
                    ARRAY[]::text[]
                ) AS opclasses,
                obj_description(idx.oid, 'pg_class') AS comment
            FROM pg_index ind
            JOIN pg_class idx ON idx.oid = ind.indexrelid
            JOIN pg_class tbl ON tbl.oid = ind.indrelid
            JOIN pg_namespace nsp ON nsp.oid = tbl.relnamespace
            JOIN pg_am am ON am.oid = idx.relam
            WHERE nsp.nspname = %s
            ORDER BY tbl.relname, idx.relname;
            """,
            (schema,),
        )
        return {self._object_key(row["table_name"], row["index_name"]): row for row in rows}

    def _read_views(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                cls.relname AS view_name,
                pg_get_viewdef(cls.oid, true) AS view_definition,
                obj_description(cls.oid, 'pg_class') AS comment
            FROM pg_class cls
            JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
            WHERE nsp.nspname = %s
              AND cls.relkind = 'v'
            ORDER BY cls.relname;
            """,
            (schema,),
        )
        return {row["view_name"]: row for row in rows}

    def _read_materialized_views(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                cls.relname AS view_name,
                pg_get_viewdef(cls.oid, true) AS view_definition,
                obj_description(cls.oid, 'pg_class') AS comment
            FROM pg_class cls
            JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
            WHERE nsp.nspname = %s
              AND cls.relkind = 'm'
            ORDER BY cls.relname;
            """,
            (schema,),
        )
        return {row["view_name"]: row for row in rows}

    def _read_sequences(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                seq_cls.relname AS sequence_name,
                format_type(seq.seqtypid, NULL) AS data_type,
                seq.seqstart AS start_value,
                seq.seqmin AS minimum_value,
                seq.seqmax AS maximum_value,
                seq.seqincrement AS increment,
                seq.seqcache AS cache_size,
                seq.seqcycle AS cycle,
                owner_cls.relname AS owner_table,
                owner_att.attname AS owner_column
            FROM pg_sequence seq
            JOIN pg_class seq_cls ON seq_cls.oid = seq.seqrelid
            JOIN pg_namespace nsp ON nsp.oid = seq_cls.relnamespace
            LEFT JOIN pg_depend dep
              ON dep.objid = seq_cls.oid
             AND dep.deptype IN ('a', 'i')
            LEFT JOIN pg_class owner_cls ON owner_cls.oid = dep.refobjid
            LEFT JOIN pg_attribute owner_att
              ON owner_att.attrelid = dep.refobjid
             AND owner_att.attnum = dep.refobjsubid
            WHERE nsp.nspname = %s
            ORDER BY seq_cls.relname;
            """,
            (schema,),
        )
        return {row["sequence_name"]: row for row in rows}

    def _read_functions(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                nsp.nspname AS schema_name,
                proc.proname AS function_name,
                pg_get_function_identity_arguments(proc.oid) AS identity_arguments,
                pg_get_function_arguments(proc.oid) AS arguments,
                pg_get_function_result(proc.oid) AS return_type,
                lang.lanname AS language,
                pg_get_functiondef(proc.oid) AS definition,
                proc.provolatile AS volatility,
                proc.prosecdef AS security_definer,
                proc.proisstrict AS strict_null_input,
                obj_description(proc.oid, 'pg_proc') AS comment
            FROM pg_proc proc
            JOIN pg_namespace nsp ON nsp.oid = proc.pronamespace
            JOIN pg_language lang ON lang.oid = proc.prolang
            WHERE nsp.nspname = %s
            ORDER BY proc.proname, pg_get_function_identity_arguments(proc.oid);
            """,
            (schema,),
        )
        return {
            f"{row['schema_name']}.{row['function_name']}({row['identity_arguments']})": row
            for row in rows
        }

    def _read_extensions(self, connection: Any) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                ext.extname AS extension_name,
                ext.extversion AS version,
                nsp.nspname AS schema_name
            FROM pg_extension ext
            JOIN pg_namespace nsp ON nsp.oid = ext.extnamespace
            ORDER BY ext.extname;
            """,
            (),
        )
        return {row["extension_name"]: row for row in rows}

    def _read_triggers(self, connection: Any, schema: str) -> Dict[str, Dict[str, Any]]:
        rows = self._fetch_all(
            connection,
            """
            SELECT
                tbl.relname AS table_name,
                trg.tgname AS trigger_name,
                pg_get_triggerdef(trg.oid, true) AS definition,
                trg.tgenabled AS enabled,
                proc_nsp.nspname AS function_schema,
                proc.proname AS function_name,
                pg_get_function_identity_arguments(proc.oid) AS function_arguments
            FROM pg_trigger trg
            JOIN pg_class tbl ON tbl.oid = trg.tgrelid
            JOIN pg_namespace nsp ON nsp.oid = tbl.relnamespace
            JOIN pg_proc proc ON proc.oid = trg.tgfoid
            JOIN pg_namespace proc_nsp ON proc_nsp.oid = proc.pronamespace
            WHERE nsp.nspname = %s
              AND NOT trg.tgisinternal
            ORDER BY tbl.relname, trg.tgname;
            """,
            (schema,),
        )
        return {self._object_key(row["table_name"], row["trigger_name"]): row for row in rows}

    def _fetch_all(self, connection: Any, query: str, params: tuple[Any, ...]) -> List[Dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def _object_key(self, table_name: str, object_name: str) -> str:
        return f"{table_name}.{object_name}"
