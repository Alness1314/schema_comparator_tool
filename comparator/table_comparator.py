from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple

from comparator.schema_comparator import SchemaComparator
from db.connection import DatabaseConnection
from db.metadata import MetadataReader


@dataclass
class TableComparisonResult:
    table_name: str
    source_name: str
    target_name: str
    source_schema: str
    target_schema: str
    source_table_name: str
    target_table_name: str
    source_schema_sql: str
    target_schema_sql: str
    generated_at: datetime
    structure_differences: List[Dict[str, Any]]
    source_row_count: int
    target_row_count: int
    data_compared: bool
    data_equal: bool
    data_skip_reason: str
    only_source_count: int
    only_target_count: int
    only_source_samples: List[Tuple[Any, ...]]
    only_target_samples: List[Tuple[Any, ...]]
    compared_columns: List[str]
    schema_sync_sql: str

    @property
    def structure_equal(self) -> bool:
        return not self.structure_differences

    @property
    def table_equal(self) -> bool:
        return self.structure_equal and self.data_compared and self.data_equal


class TableComparator:
    """Compara estructura y datos de una tabla entre ORIGEN y DESTINO."""

    def __init__(
        self,
        source_connection: DatabaseConnection,
        target_connection: DatabaseConnection,
        source_name: str = "ORIGEN",
        target_name: str = "DESTINO",
        sample_size: int = 20,
    ) -> None:
        self.source_connection = source_connection
        self.target_connection = target_connection
        self.source_name = source_name
        self.target_name = target_name
        self.sample_size = sample_size
        self.schema_comparator = SchemaComparator(
            MetadataReader(source_connection),
            MetadataReader(target_connection),
        )

    def compare(self, table_name: str) -> TableComparisonResult:
        clean_name = table_name.strip()
        if not clean_name:
            raise ValueError("Escribe el nombre de la tabla.")

        source_metadata = MetadataReader(self.source_connection).read()
        target_metadata = MetadataReader(self.target_connection).read()
        source_table_name = self._resolve_table_name(source_metadata, clean_name, self.source_name)
        target_table_name = self._resolve_table_name(target_metadata, clean_name, self.target_name)

        structure_differences = self._compare_structure(
            source_metadata,
            target_metadata,
            source_table_name,
            target_table_name,
        )
        schema_sync_sql = self._schema_sync_sql(
            source_metadata,
            target_metadata,
            source_table_name,
            target_table_name,
        )
        data_result = self._compare_data(
            source_metadata,
            target_metadata,
            source_table_name,
            target_table_name,
        )

        return TableComparisonResult(
            table_name=clean_name,
            source_name=self.source_name,
            target_name=self.target_name,
            source_schema=source_metadata["schema"],
            target_schema=target_metadata["schema"],
            source_table_name=source_table_name,
            target_table_name=target_table_name,
            source_schema_sql=self._table_schema_sql(source_metadata, source_table_name),
            target_schema_sql=self._table_schema_sql(target_metadata, target_table_name),
            generated_at=datetime.now(),
            structure_differences=structure_differences,
            schema_sync_sql=schema_sync_sql,
            **data_result,
        )

    def _resolve_table_name(
        self,
        metadata: Dict[str, Any],
        table_name: str,
        connection_name: str,
    ) -> str:
        if table_name in metadata["tables"]:
            return table_name

        matches = [
            candidate
            for candidate in metadata["tables"]
            if candidate.lower() == table_name.lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(
                f"No existe la tabla '{table_name}' en {connection_name}."
            )
        raise ValueError(
            f"El nombre '{table_name}' coincide con varias tablas en {connection_name}. "
            "Usa la capitalizacion exacta."
        )

    def _compare_structure(
        self,
        source: Dict[str, Any],
        target: Dict[str, Any],
        source_table_name: str,
        target_table_name: str,
    ) -> List[Dict[str, Any]]:
        differences: List[Dict[str, Any]] = []
        source_columns = source["columns"].get(source_table_name, {})
        target_columns = target["columns"].get(target_table_name, {})

        for column_name, source_column in sorted(
            source_columns.items(),
            key=lambda item: item[1]["ordinal_position"],
        ):
            target_column = target_columns.get(column_name)
            if target_column is None:
                differences.append(
                    self._difference(
                        "COLUMN_MISSING",
                        f"La columna '{column_name}' existe en {self.source_name} "
                        f"y no existe en {self.target_name}.",
                        "CRITICA" if source_column["is_nullable"] == "NO" else "MEDIO",
                    )
                )
            elif self.schema_comparator._column_signature(source_column) != self.schema_comparator._column_signature(target_column):
                differences.append(
                    self._difference(
                        "COLUMN_DIFFERENT",
                        f"La columna '{column_name}' tiene definicion distinta.",
                        "ALTO",
                    )
                )

        for column_name in sorted(set(target_columns) - set(source_columns)):
            differences.append(
                self._difference(
                    "EXTRA_COLUMN",
                    f"La columna '{column_name}' existe en {self.target_name} "
                    f"y no existe en {self.source_name}.",
                    "MEDIO",
                )
            )

        differences.extend(
            self._compare_objects(
                "constraints",
                source,
                target,
                source_table_name,
                target_table_name,
                "CONSTRAINT_DIFFERENT",
                "constraint_name",
                "definition",
            )
        )
        source_constraint_names = self._constraint_names(source, source_table_name)
        target_constraint_names = self._constraint_names(target, target_table_name)
        differences.extend(
            self._compare_objects(
                "indexes",
                source,
                target,
                source_table_name,
                target_table_name,
                "INDEX_DIFFERENT",
                "index_name",
                "indexdef",
                source_constraint_names,
                target_constraint_names | source_constraint_names,
            )
        )
        return differences

    def _schema_sync_sql(
        self,
        source: Dict[str, Any],
        target: Dict[str, Any],
        source_table_name: str,
        target_table_name: str,
    ) -> str:
        target_schema = target["schema"]
        source_columns = source["columns"].get(source_table_name, {})
        target_columns = target["columns"].get(target_table_name, {})
        statements: List[str] = []

        if target_table_name not in target["tables"]:
            return self._table_schema_sql(source, source_table_name).replace(
                self._qualified(source["schema"], source_table_name),
                self._qualified(target_schema, source_table_name),
            )

        for column_name, source_column in sorted(
            source_columns.items(),
            key=lambda item: item[1]["ordinal_position"],
        ):
            target_column = target_columns.get(column_name)
            if target_column is None:
                statements.append(
                    f"ALTER TABLE {self._qualified(target_schema, target_table_name)} "
                    f"ADD COLUMN {self._q(column_name)} "
                    f"{self.schema_comparator._column_type(source_column)}"
                    f"{self.schema_comparator._default_sql(source_column)}"
                    f"{self.schema_comparator._null_sql(source_column)};"
                )
            elif self.schema_comparator._column_signature(source_column) != self.schema_comparator._column_signature(target_column):
                statements.append(
                    self.schema_comparator._alter_column_sql(
                        target_schema,
                        target_table_name,
                        column_name,
                        source_column,
                    )
                )

        source_constraints = {
            item["constraint_name"]: item
            for item in source["constraints"].values()
            if item["table_name"] == source_table_name
            and item["constraint_type"] != "n"
        }
        target_constraints = {
            item["constraint_name"]: item
            for item in target["constraints"].values()
            if item["table_name"] == target_table_name
            and item["constraint_type"] != "n"
        }
        target_constraint_signatures = {
            self._constraint_signature(item)
            for item in target_constraints.values()
        }
        for constraint_name, source_constraint in sorted(source_constraints.items()):
            target_constraint = target_constraints.get(constraint_name)
            source_signature = self._constraint_signature(source_constraint)
            if target_constraint is None and source_signature in target_constraint_signatures:
                continue
            add_sql = (
                f"ALTER TABLE {self._qualified(target_schema, target_table_name)} "
                f"ADD CONSTRAINT {self._q(constraint_name)} "
                f"{source_constraint['definition']};"
            )
            if target_constraint is None:
                target_index = self._index_by_name(target, target_table_name, constraint_name)
                if target_index and source_constraint["constraint_type"] in {"p", "u"}:
                    constraint_kind = (
                        "PRIMARY KEY"
                        if source_constraint["constraint_type"] == "p"
                        else "UNIQUE"
                    )
                    statements.append(
                        f"ALTER TABLE {self._qualified(target_schema, target_table_name)} "
                        f"ADD CONSTRAINT {self._q(constraint_name)} "
                        f"{constraint_kind} USING INDEX {self._q(constraint_name)};"
                    )
                else:
                    statements.append(add_sql)
            elif self._constraint_signature(target_constraint) != source_signature:
                statements.append(
                    f"ALTER TABLE {self._qualified(target_schema, target_table_name)} "
                    f"DROP CONSTRAINT {self._q(constraint_name)};\n{add_sql}"
                )

        source_indexes = {
            item["index_name"]: item
            for item in source["indexes"].values()
            if item["table_name"] == source_table_name
            and item["index_name"] not in source_constraints
        }
        target_indexes = {
            item["index_name"]: item
            for item in target["indexes"].values()
            if item["table_name"] == target_table_name
            and item["index_name"] not in target_constraints
        }
        constraint_names = set(source_constraints)
        for index_name, source_index in sorted(source_indexes.items()):
            if index_name in constraint_names:
                continue
            target_index = target_indexes.get(index_name)
            if target_index and target_index["indexdef"] == source_index["indexdef"]:
                continue
            create_sql = source_index["indexdef"].rstrip(";").replace(
                f"{self._q(source['schema'])}.",
                f"{self._q(target_schema)}.",
            )
            if target_index is None:
                statements.append(f"{create_sql};")
            else:
                statements.append(
                    f"DROP INDEX {self._qualified(target_schema, index_name)};\n{create_sql};"
                )

        return "\n\n".join(statements)

    def _compare_objects(
        self,
        collection_name: str,
        source: Dict[str, Any],
        target: Dict[str, Any],
        source_table_name: str,
        target_table_name: str,
        difference_type: str,
        name_field: str,
        signature_field: str,
        source_ignored_names: set[str] | None = None,
        target_ignored_names: set[str] | None = None,
    ) -> List[Dict[str, Any]]:
        differences: List[Dict[str, Any]] = []
        source_ignored_names = source_ignored_names or set()
        target_ignored_names = target_ignored_names or set()
        source_objects = {
            item[name_field]: item
            for item in source[collection_name].values()
            if item["table_name"] == source_table_name
            and item[name_field] not in source_ignored_names
            and item.get("constraint_type") != "n"
        }
        target_objects = {
            item[name_field]: item
            for item in target[collection_name].values()
            if item["table_name"] == target_table_name
            and item[name_field] not in target_ignored_names
            and item.get("constraint_type") != "n"
        }
        target_signatures = {
            self._object_signature(collection_name, item, signature_field)
            for item in target_objects.values()
        }
        source_signatures = {
            self._object_signature(collection_name, item, signature_field)
            for item in source_objects.values()
        }
        for object_name, source_object in sorted(source_objects.items()):
            target_object = target_objects.get(object_name)
            source_signature = self._object_signature(
                collection_name,
                source_object,
                signature_field,
            )
            if target_object is None:
                if source_signature in target_signatures:
                    continue
                differences.append(
                    self._difference(
                        difference_type,
                        f"'{object_name}' existe en {self.source_name} "
                        f"y no existe en {self.target_name}.",
                        "ALTO",
                    )
                )
            elif source_signature != self._object_signature(
                collection_name,
                target_object,
                signature_field,
            ):
                differences.append(
                    self._difference(
                        difference_type,
                        f"'{object_name}' tiene definicion distinta.",
                        "ALTO",
                    )
                )

        for object_name in sorted(set(target_objects) - set(source_objects)):
            if self._object_signature(
                collection_name,
                target_objects[object_name],
                signature_field,
            ) in source_signatures:
                continue
            differences.append(
                self._difference(
                    difference_type,
                    f"'{object_name}' existe en {self.target_name} "
                    f"y no existe en {self.source_name}.",
                    "MEDIO",
                )
            )
        return differences

    def _compare_data(
        self,
        source_metadata: Dict[str, Any],
        target_metadata: Dict[str, Any],
        source_table_name: str,
        target_table_name: str,
    ) -> Dict[str, Any]:
        source_columns = source_metadata["columns"].get(source_table_name, {})
        target_columns = target_metadata["columns"].get(target_table_name, {})
        source_column_names = self._ordered_column_names(source_columns)
        target_column_names = self._ordered_column_names(target_columns)
        if set(source_column_names) != set(target_column_names):
            source_row_count = self._count_rows(
                self.source_connection,
                source_metadata["schema"],
                source_table_name,
            )
            target_row_count = self._count_rows(
                self.target_connection,
                target_metadata["schema"],
                target_table_name,
            )
            return {
                "source_row_count": source_row_count,
                "target_row_count": target_row_count,
                "data_compared": False,
                "data_equal": False,
                "data_skip_reason": (
                    "No se compararon datos porque las columnas no son iguales "
                    f"entre {self.source_name} y {self.target_name}."
                ),
                "only_source_count": 0,
                "only_target_count": 0,
                "only_source_samples": [],
                "only_target_samples": [],
                "compared_columns": [],
            }
        target_column_names = source_column_names

        source_counter = self._read_rows(
            self.source_connection,
            source_metadata["schema"],
            source_table_name,
            source_column_names,
        )
        target_counter = self._read_rows(
            self.target_connection,
            target_metadata["schema"],
            target_table_name,
            target_column_names,
        )
        only_source = source_counter - target_counter
        only_target = target_counter - source_counter
        return {
            "source_row_count": sum(source_counter.values()),
            "target_row_count": sum(target_counter.values()),
            "data_compared": True,
            "data_equal": not only_source and not only_target,
            "data_skip_reason": "",
            "only_source_count": sum(only_source.values()),
            "only_target_count": sum(only_target.values()),
            "only_source_samples": self._samples(only_source),
            "only_target_samples": self._samples(only_target),
            "compared_columns": source_column_names,
        }

    def _read_rows(
        self,
        db_connection: DatabaseConnection,
        schema: str,
        table_name: str,
        columns: List[str],
    ) -> Counter[Tuple[Any, ...]]:
        connection = db_connection.connect()
        cursor = connection.cursor()
        try:
            selected_columns = ", ".join(self._q(column) for column in columns)
            cursor.execute(
                f"SELECT {selected_columns} FROM {self._qualified(schema, table_name)}"
            )
            rows: Counter[Tuple[Any, ...]] = Counter()
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for row in batch:
                    rows[self._row_tuple(row, columns)] += 1
            return rows
        finally:
            cursor.close()
            db_connection.close()

    def _read_row_list(
        self,
        db_connection: DatabaseConnection,
        schema: str,
        table_name: str,
        columns: List[str],
    ) -> List[Tuple[Any, ...]]:
        connection = db_connection.connect()
        cursor = connection.cursor()
        try:
            selected_columns = ", ".join(self._q(column) for column in columns)
            cursor.execute(
                f"SELECT {selected_columns} FROM {self._qualified(schema, table_name)}"
            )
            rows: List[Tuple[Any, ...]] = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                rows.extend(self._row_tuple(row, columns) for row in batch)
            return rows
        finally:
            cursor.close()
            db_connection.close()

    def _count_rows(
        self,
        db_connection: DatabaseConnection,
        schema: str,
        table_name: str,
    ) -> int:
        connection = db_connection.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) AS total FROM {self._qualified(schema, table_name)}")
            row = cursor.fetchone()
            if isinstance(row, dict):
                return int(row["total"])
            return int(row[0])
        finally:
            cursor.close()
            db_connection.close()

    def _row_tuple(self, row: Any, columns: List[str]) -> Tuple[Any, ...]:
        if isinstance(row, dict):
            return tuple(row[column] for column in columns)
        return tuple(row)

    def _samples(self, counter: Counter[Tuple[Any, ...]]) -> List[Tuple[Any, ...]]:
        samples: List[Tuple[Any, ...]] = []
        for row, count in counter.items():
            for _ in range(min(count, self.sample_size - len(samples))):
                samples.append(row)
                if len(samples) >= self.sample_size:
                    return samples
        return samples

    def _ordered_column_names(self, columns: Dict[str, Dict[str, Any]]) -> List[str]:
        return [
            column_name
            for column_name, _column in sorted(
                columns.items(),
                key=lambda item: item[1]["ordinal_position"],
            )
        ]

    def _table_schema_sql(self, metadata: Dict[str, Any], table_name: str) -> str:
        schema = metadata["schema"]
        columns = metadata["columns"].get(table_name, {})
        column_lines = [
            (
                f"    {self._q(column_name)} "
                f"{self.schema_comparator._column_type(column)}"
                f"{self.schema_comparator._default_sql(column)}"
                f"{self.schema_comparator._null_sql(column)}"
            )
            for column_name, column in sorted(
                columns.items(),
                key=lambda item: item[1]["ordinal_position"],
            )
        ]
        create_table = (
            f"CREATE TABLE {self._qualified(schema, table_name)} (\n"
            + ",\n".join(column_lines)
            + "\n);"
        )
        constraints = [
            (
                f"ALTER TABLE {self._qualified(schema, constraint['table_name'])} "
                f"ADD CONSTRAINT {self._q(constraint['constraint_name'])} "
                f"{constraint['definition']};"
            )
            for constraint in sorted(
                metadata["constraints"].values(),
                key=lambda item: item["constraint_name"],
            )
            if constraint["table_name"] == table_name
            and constraint["constraint_type"] != "n"
        ]
        constraint_names = {
            constraint["constraint_name"]
            for constraint in metadata["constraints"].values()
            if constraint["table_name"] == table_name
            and constraint["constraint_type"] != "n"
        }
        indexes = [
            f"{index['indexdef'].rstrip(';')};"
            for index in sorted(
                metadata["indexes"].values(),
                key=lambda item: item["index_name"],
            )
            if index["table_name"] == table_name
            and index["index_name"] not in constraint_names
        ]
        sections = [create_table]
        if constraints:
            sections.extend(constraints)
        if indexes:
            sections.extend(indexes)
        return "\n\n".join(sections)

    def _constraint_names(self, metadata: Dict[str, Any], table_name: str) -> set[str]:
        return {
            constraint["constraint_name"]
            for constraint in metadata["constraints"].values()
            if constraint["table_name"] == table_name
            and constraint["constraint_type"] != "n"
        }

    def _index_by_name(
        self,
        metadata: Dict[str, Any],
        table_name: str,
        index_name: str,
    ) -> Dict[str, Any] | None:
        for index in metadata["indexes"].values():
            if index["table_name"] == table_name and index["index_name"] == index_name:
                return index
        return None

    def _object_signature(
        self,
        collection_name: str,
        item: Dict[str, Any],
        signature_field: str,
    ) -> str:
        if collection_name == "constraints" and signature_field == "definition":
            return self._constraint_signature(item)
        return str(item[signature_field])

    def _constraint_signature(self, constraint: Dict[str, Any]) -> str:
        definition = str(constraint["definition"])
        return (
            definition
            .replace("::character varying::text", "::character varying")
            .replace("::text[]", "")
            .replace(" ", "")
            .lower()
        )

    def _difference(
        self,
        difference_type: str,
        description: str,
        impact: str,
    ) -> Dict[str, Any]:
        return {
            "type": difference_type,
            "impact": impact,
            "description": description,
        }

    def _qualified(self, schema: str, name: str) -> str:
        return f"{self._q(schema)}.{self._q(name)}"

    def _q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'


@dataclass
class TableSyncResult:
    table_name: str
    source_name: str
    target_name: str
    sync_type: str
    generated_at: datetime
    statements_executed: int
    rows_copied: int
    status: str
    detail: str


class TableSynchronizer:
    """Iguala schema o data de una tabla segun la direccion seleccionada."""

    def __init__(
        self,
        source_connection: DatabaseConnection,
        target_connection: DatabaseConnection,
        source_name: str = "ORIGEN",
        target_name: str = "DESTINO",
    ) -> None:
        self.source_connection = source_connection
        self.target_connection = target_connection
        self.source_name = source_name
        self.target_name = target_name

    def sync_schema(self, table_name: str) -> TableSyncResult:
        comparator = TableComparator(
            self.source_connection,
            self.target_connection,
            self.source_name,
            self.target_name,
        )
        result = comparator.compare(table_name)
        if not result.schema_sync_sql.strip():
            return TableSyncResult(
                table_name=table_name,
                source_name=self.source_name,
                target_name=self.target_name,
                sync_type="SCHEMA",
                generated_at=datetime.now(),
                statements_executed=0,
                rows_copied=0,
                status="SIN CAMBIOS",
                detail="El schema de la tabla ya coincide.",
            )

        statements = self._split_sql(result.schema_sync_sql)
        connection = None
        try:
            connection = self.target_connection.connect()
            cursor = connection.cursor()
            try:
                for statement in statements:
                    cursor.execute(statement)
            finally:
                cursor.close()
            connection.commit()
            return TableSyncResult(
                table_name=table_name,
                source_name=self.source_name,
                target_name=self.target_name,
                sync_type="SCHEMA",
                generated_at=datetime.now(),
                statements_executed=len(statements),
                rows_copied=0,
                status="COMPLETADO",
                detail="Schema igualado correctamente.",
            )
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            self.source_connection.close()
            self.target_connection.close()

    def sync_data(self, table_name: str) -> TableSyncResult:
        source_metadata = MetadataReader(self.source_connection).read()
        target_metadata = MetadataReader(self.target_connection).read()
        comparator = TableComparator(
            self.source_connection,
            self.target_connection,
            self.source_name,
            self.target_name,
        )
        source_table_name = comparator._resolve_table_name(source_metadata, table_name, self.source_name)
        target_table_name = comparator._resolve_table_name(target_metadata, table_name, self.target_name)
        source_columns = comparator._ordered_column_names(
            source_metadata["columns"].get(source_table_name, {})
        )
        target_columns = comparator._ordered_column_names(
            target_metadata["columns"].get(target_table_name, {})
        )
        if set(source_columns) != set(target_columns):
            raise ValueError(
                "No se puede igualar data porque las columnas no son iguales. "
                "Primero iguala el schema."
            )
        target_columns = source_columns
        self._validate_target_fk_parents(
            source_metadata,
            target_metadata,
            source_table_name,
        )

        rows = comparator._read_row_list(
            self.source_connection,
            source_metadata["schema"],
            source_table_name,
            source_columns,
        )
        connection = None
        try:
            connection = self.target_connection.connect()
            cursor = connection.cursor()
            qualified_table = comparator._qualified(target_metadata["schema"], target_table_name)
            selected_columns = ", ".join(comparator._q(column) for column in source_columns)
            placeholders = ", ".join(self._placeholder() for _ in source_columns)
            try:
                cursor.execute(f"DELETE FROM {qualified_table}")
                if rows:
                    insert_sql = (
                        f"INSERT INTO {qualified_table} ({selected_columns}) "
                        f"VALUES ({placeholders})"
                    )
                    for row in rows:
                        cursor.execute(insert_sql, row)
            finally:
                cursor.close()
            connection.commit()
            return TableSyncResult(
                table_name=table_name,
                source_name=self.source_name,
                target_name=self.target_name,
                sync_type="DATA",
                generated_at=datetime.now(),
                statements_executed=1 + len(rows),
                rows_copied=len(rows),
                status="COMPLETADO",
                detail="Data igualada correctamente.",
            )
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            self.source_connection.close()
            self.target_connection.close()

    def _validate_target_fk_parents(
        self,
        source_metadata: Dict[str, Any],
        target_metadata: Dict[str, Any],
        source_table_name: str,
    ) -> None:
        fk_constraints = [
            constraint
            for constraint in source_metadata["constraints"].values()
            if constraint["table_name"] == source_table_name
            and constraint["constraint_type"] == "f"
        ]
        missing_messages: List[str] = []
        for constraint in fk_constraints:
            parent_table = constraint["referenced_table"]
            if parent_table not in target_metadata["tables"]:
                missing_messages.append(
                    f"{constraint['constraint_name']}: falta la tabla padre {parent_table} en {self.target_name}."
                )
                continue

            missing_count = self._missing_parent_references(
                source_metadata["schema"],
                source_table_name,
                constraint["columns"],
                target_metadata["schema"],
                parent_table,
                constraint["referenced_columns"],
            )
            if missing_count:
                missing_messages.append(
                    f"{constraint['constraint_name']}: {missing_count} referencia(s) "
                    f"no existen en {self.target_name}.{parent_table}."
                )

        if missing_messages:
            detail = "\n".join(f"- {message}" for message in missing_messages)
            raise ValueError(
                "No se puede igualar data porque faltan registros padre en el destino.\n"
                f"{detail}\n"
                "Primero iguala o carga esas tablas relacionadas."
            )

    def _missing_parent_references(
        self,
        source_schema: str,
        source_table_name: str,
        child_columns: List[str],
        target_schema: str,
        parent_table: str,
        parent_columns: List[str],
    ) -> int:
        source_connection = self.source_connection.connect()
        source_cursor = source_connection.cursor()
        try:
            selected_columns = ", ".join(self._q(column) for column in child_columns)
            not_null_clause = " AND ".join(
                f"{self._q(column)} IS NOT NULL" for column in child_columns
            )
            source_cursor.execute(
                f"SELECT DISTINCT {selected_columns} "
                f"FROM {self._qualified(source_schema, source_table_name)} "
                f"WHERE {not_null_clause}"
            )
            references = source_cursor.fetchall()
        finally:
            source_cursor.close()
            self.source_connection.close()

        missing_count = 0
        target_connection = self.target_connection.connect()
        try:
            for row in references:
                values = self._row_tuple(row, child_columns)
                where_clause = " AND ".join(
                    f"{self._q(column)} = {self._placeholder()}"
                    for column in parent_columns
                )
                target_cursor = target_connection.cursor()
                try:
                    target_cursor.execute(
                        f"SELECT COUNT(*) AS total "
                        f"FROM {self._qualified(target_schema, parent_table)} "
                        f"WHERE {where_clause}",
                        values,
                    )
                    count_row = target_cursor.fetchone()
                    count = count_row["total"] if isinstance(count_row, dict) else count_row[0]
                    if not count:
                        missing_count += 1
                finally:
                    target_cursor.close()
        finally:
            self.target_connection.close()
        return missing_count

    def _placeholder(self) -> str:
        engine = self.target_connection.parsed_url["engine"]
        return "?" if engine == "sqlserver" else "%s"

    def _qualified(self, schema: str, name: str) -> str:
        return f"{self._q(schema)}.{self._q(name)}"

    def _q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _row_tuple(self, row: Any, columns: List[str]) -> Tuple[Any, ...]:
        if isinstance(row, dict):
            return tuple(row[column] for column in columns)
        return tuple(row)

    def _split_sql(self, sql: str) -> List[str]:
        return [
            statement.strip()
            for statement in sql.split(";")
            if statement.strip()
        ]
