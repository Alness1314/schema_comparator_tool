from typing import Any, Dict, List

from db.connection import DatabaseConnection


class DataIntegrityChecker:
    """Revisa problemas de datos que pueden bloquear constraints de integridad."""

    AUDIT_RELATION_COLUMNS = {"id_usuario_modificacion"}

    def __init__(
        self,
        target_connection: DatabaseConnection,
        source_metadata: Dict[str, Any],
        target_metadata: Dict[str, Any],
    ) -> None:
        self.target_connection = target_connection
        self.source_metadata = source_metadata
        self.target_metadata = target_metadata

    def check(self) -> List[Dict[str, Any]]:
        try:
            engine = self.target_connection.parsed_url["engine"]
            if engine != "postgresql":
                raise NotImplementedError(
                    "La revision de integridad de datos esta implementada por ahora para PostgreSQL."
                )

            connection = self.target_connection.connect()
            findings: List[Dict[str, Any]] = []

            findings.extend(self._check_required_nulls(connection))
            findings.extend(self._check_fk_nulls(connection))
            findings.extend(self._check_fk_orphans(connection))
            findings.extend(self._check_unique_duplicates(connection))
            findings.extend(self._check_required_empty_strings(connection))

            return findings
        finally:
            self.target_connection.close()

    def _check_required_nulls(self, connection: Any) -> List[Dict[str, Any]]:
        findings = []
        for table_name, source_columns in sorted(self.source_metadata["columns"].items()):
            if table_name not in self.target_metadata["tables"]:
                continue

            target_columns = self.target_metadata["columns"].get(table_name, {})
            for column_name, column in sorted(source_columns.items()):
                if column["is_nullable"] != "NO" or column_name not in target_columns:
                    continue

                count = self._count(
                    connection,
                    (
                        f"SELECT COUNT(*) AS total "
                        f"FROM {self._qualified(self.target_metadata['schema'], table_name)} "
                        f"WHERE {self._q(column_name)} IS NULL;"
                    ),
                )
                if count:
                    findings.append(
                        self._finding(
                            "REQUIRED_NULL_VALUES",
                            "CRITICA",
                            (
                                f"La columna obligatoria '{table_name}.{column_name}' tiene "
                                f"{count} fila(s) NULL en DESTINO."
                            ),
                            "Al aplicar NOT NULL o depender de esa columna, estas filas rompen integridad de datos.",
                            self._null_review_sql(table_name, [column_name]),
                            count,
                        )
                    )
        return findings

    def _check_fk_nulls(self, connection: Any) -> List[Dict[str, Any]]:
        findings = []
        for constraint in self._source_constraints("f"):
            table_name = constraint["table_name"]
            columns = constraint["columns"]
            if not self._has_target_columns(table_name, columns):
                continue

            nullable_columns = [
                column_name
                for column_name in columns
                if self.source_metadata["columns"][table_name][column_name]["is_nullable"] == "YES"
            ]
            if not nullable_columns:
                continue

            count = self._count(
                connection,
                (
                    f"SELECT COUNT(*) AS total "
                    f"FROM {self._qualified(self.target_metadata['schema'], table_name)} "
                    f"WHERE {self._any_null_sql(columns)};"
                ),
            )
            if count:
                impact = self._fk_relation_impact(columns, "ALTO")
                findings.append(
                    self._finding(
                        "FK_NULL_RELATION",
                        impact,
                        (
                            f"La relacion '{constraint['constraint_name']}' en '{table_name}' "
                            f"tiene {count} fila(s) con columnas FK en NULL."
                        ),
                        self._fk_relation_reason(
                            columns,
                            "Aunque el schema lo permita, una relacion NULL puede dejar registros sin padre funcional.",
                        ),
                        self._null_review_sql(table_name, columns),
                        count,
                    )
                )
        return findings

    def _check_fk_orphans(self, connection: Any) -> List[Dict[str, Any]]:
        findings = []
        for constraint in self._source_constraints("f"):
            table_name = constraint["table_name"]
            child_columns = constraint["columns"]
            parent_table = constraint["referenced_table"]
            parent_columns = constraint["referenced_columns"]
            if (
                not parent_table
                or not self._has_target_columns(table_name, child_columns)
                or not self._has_target_columns(parent_table, parent_columns)
            ):
                continue

            child = self._qualified(self.target_metadata["schema"], table_name)
            parent = self._qualified(self.target_metadata["schema"], parent_table)
            join_conditions = " AND ".join(
                f"p.{self._q(parent_col)} = c.{self._q(child_col)}"
                for child_col, parent_col in zip(child_columns, parent_columns)
            )
            count = self._count(
                connection,
                (
                    f"SELECT COUNT(*) AS total "
                    f"FROM {child} c "
                    f"WHERE {self._all_not_null_sql('c', child_columns)} "
                    f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {join_conditions});"
                ),
            )
            if count:
                fix_sql = self._orphan_fix_sql(
                    table_name,
                    child_columns,
                    parent_table,
                    parent_columns,
                )
                impact = self._fk_relation_impact(child_columns, "CRITICA")
                findings.append(
                    self._finding(
                        "FK_ORPHAN_ROWS",
                        impact,
                        (
                            f"La relacion '{constraint['constraint_name']}' tiene {count} fila(s) "
                            f"en '{table_name}' sin registro padre en '{parent_table}'."
                        ),
                        self._fk_relation_reason(
                            child_columns,
                            "Estas filas romperian la llave foranea y comprometen integridad referencial.",
                        ),
                        self._orphan_review_sql(table_name, child_columns, parent_table, parent_columns),
                        count,
                        fix_sql,
                    )
                )
        return findings

    def _check_unique_duplicates(self, connection: Any) -> List[Dict[str, Any]]:
        findings = []
        for constraint in self._source_constraints("p", "u"):
            table_name = constraint["table_name"]
            columns = constraint["columns"]
            if not self._has_target_columns(table_name, columns):
                continue

            table = self._qualified(self.target_metadata["schema"], table_name)
            group_columns = ", ".join(self._q(column_name) for column_name in columns)
            where_clause = ""
            if constraint["constraint_type"] == "u":
                where_clause = f"WHERE {self._all_not_null_sql('', columns)} "
            count = self._count(
                connection,
                (
                    "SELECT COALESCE(SUM(duplicate_rows - 1), 0) AS total "
                    "FROM ("
                    f"SELECT COUNT(*) AS duplicate_rows FROM {table} "
                    f"{where_clause}"
                    f"GROUP BY {group_columns} HAVING COUNT(*) > 1"
                    ") duplicated;"
                ),
            )
            if count:
                findings.append(
                    self._finding(
                        "DUPLICATE_KEY_VALUES",
                        "CRITICA",
                        (
                            f"La constraint '{constraint['constraint_name']}' en '{table_name}' "
                            f"tiene {count} fila(s) duplicada(s) excedentes."
                        ),
                        "Los duplicados romperian PRIMARY KEY o UNIQUE y pueden duplicar entidades de negocio.",
                        self._duplicates_review_sql(
                            table_name,
                            columns,
                            constraint["constraint_type"] == "u",
                        ),
                        count,
                    )
                )
        return findings

    def _check_required_empty_strings(self, connection: Any) -> List[Dict[str, Any]]:
        findings = []
        text_types = {"character varying", "character", "text"}
        for table_name, source_columns in sorted(self.source_metadata["columns"].items()):
            if table_name not in self.target_metadata["tables"]:
                continue

            target_columns = self.target_metadata["columns"].get(table_name, {})
            for column_name, column in sorted(source_columns.items()):
                if (
                    column["is_nullable"] != "NO"
                    or column["data_type"] not in text_types
                    or column_name not in target_columns
                ):
                    continue

                count = self._count(
                    connection,
                    (
                        f"SELECT COUNT(*) AS total "
                        f"FROM {self._qualified(self.target_metadata['schema'], table_name)} "
                        f"WHERE btrim({self._q(column_name)}) = '';"
                    ),
                )
                if count:
                    findings.append(
                        self._finding(
                            "REQUIRED_EMPTY_TEXT",
                            "MEDIO",
                            (
                                f"La columna obligatoria de texto '{table_name}.{column_name}' "
                                f"tiene {count} fila(s) vacia(s)."
                            ),
                            "No es NULL, pero suele indicar datos obligatorios incompletos.",
                            (
                                f"SELECT * FROM {self._qualified(self.target_metadata['schema'], table_name)} "
                                f"WHERE btrim({self._q(column_name)}) = '' LIMIT 50;"
                            ),
                            count,
                        )
                    )
        return findings

    def _source_constraints(self, *constraint_types: str) -> List[Dict[str, Any]]:
        return [
            constraint
            for constraint in self.source_metadata["constraints"].values()
            if constraint["constraint_type"] in constraint_types
        ]

    def _has_target_columns(self, table_name: str, columns: List[str]) -> bool:
        target_columns = self.target_metadata["columns"].get(table_name, {})
        return table_name in self.target_metadata["tables"] and all(
            column_name in target_columns for column_name in columns
        )

    def _count(self, connection: Any, query: str) -> int:
        with connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()
            return int(row["total"] or 0)

    def _finding(
        self,
        finding_type: str,
        impact: str,
        description: str,
        impact_reason: str,
        review_sql: str,
        count: int,
        fix_sql: str = "",
    ) -> Dict[str, Any]:
        return {
            "type": finding_type,
            "impact": impact,
            "impact_reason": impact_reason,
            "description": description,
            "review_sql": review_sql,
            "count": count,
            "fix_sql": fix_sql,
        }

    def _fk_relation_impact(self, columns: List[str], default_impact: str) -> str:
        if self._is_audit_relation(columns):
            return "BAJO"
        return default_impact

    def _fk_relation_reason(self, columns: List[str], default_reason: str) -> str:
        if self._is_audit_relation(columns):
            return (
                "Relacion de auditoria por id_usuario_modificacion; se deja en BAJO "
                "porque no es relevante para la integridad funcional principal."
            )
        return default_reason

    def _is_audit_relation(self, columns: List[str]) -> bool:
        return any(column_name in self.AUDIT_RELATION_COLUMNS for column_name in columns)

    def _null_review_sql(self, table_name: str, columns: List[str]) -> str:
        return (
            f"SELECT * FROM {self._qualified(self.target_metadata['schema'], table_name)} "
            f"WHERE {self._any_null_sql(columns)} LIMIT 50;"
        )

    def _orphan_review_sql(
        self,
        table_name: str,
        child_columns: List[str],
        parent_table: str,
        parent_columns: List[str],
    ) -> str:
        child = self._qualified(self.target_metadata["schema"], table_name)
        parent = self._qualified(self.target_metadata["schema"], parent_table)
        join_conditions = " AND ".join(
            f"p.{self._q(parent_col)} = c.{self._q(child_col)}"
            for child_col, parent_col in zip(child_columns, parent_columns)
        )
        return (
            f"SELECT c.* FROM {child} c "
            f"WHERE {self._all_not_null_sql('c', child_columns)} "
            f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {join_conditions}) "
            "LIMIT 50;"
        )

    def _orphan_fix_sql(
        self,
        table_name: str,
        child_columns: List[str],
        parent_table: str,
        parent_columns: List[str],
    ) -> str:
        nullable_columns = [
            column_name
            for column_name in child_columns
            if self.target_metadata["columns"][table_name][column_name]["is_nullable"] == "YES"
        ]
        if len(nullable_columns) != len(child_columns):
            return (
                "-- Fix automatico no generado: una o mas columnas FK no permiten NULL. "
                "Corrige insertando el padre faltante o reasignando la FK a un registro valido."
            )

        child = self._qualified(self.target_metadata["schema"], table_name)
        parent = self._qualified(self.target_metadata["schema"], parent_table)
        set_clause = ", ".join(f"{self._q(column_name)} = NULL" for column_name in child_columns)
        join_conditions = " AND ".join(
            f"p.{self._q(parent_col)} = c.{self._q(child_col)}"
            for child_col, parent_col in zip(child_columns, parent_columns)
        )
        return (
            f"UPDATE {child} c\n"
            f"SET {set_clause}\n"
            f"WHERE {self._all_not_null_sql('c', child_columns)}\n"
            f"  AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {join_conditions});"
        )

    def _duplicates_review_sql(
        self,
        table_name: str,
        columns: List[str],
        ignore_null_groups: bool,
    ) -> str:
        table = self._qualified(self.target_metadata["schema"], table_name)
        selected_columns = ", ".join(self._q(column_name) for column_name in columns)
        where_clause = ""
        if ignore_null_groups:
            where_clause = f"WHERE {self._all_not_null_sql('', columns)} "
        return (
            f"SELECT {selected_columns}, COUNT(*) AS duplicate_rows FROM {table} "
            f"{where_clause}"
            f"GROUP BY {selected_columns} HAVING COUNT(*) > 1 "
            "ORDER BY duplicate_rows DESC LIMIT 50;"
        )

    def _any_null_sql(self, columns: List[str]) -> str:
        return " OR ".join(f"{self._q(column_name)} IS NULL" for column_name in columns)

    def _all_not_null_sql(self, alias: str, columns: List[str]) -> str:
        prefix = f"{alias}." if alias else ""
        return " AND ".join(f"{prefix}{self._q(column_name)} IS NOT NULL" for column_name in columns)

    def _qualified(self, schema: str, name: str) -> str:
        return f"{self._q(schema)}.{self._q(name)}"

    def _q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'
