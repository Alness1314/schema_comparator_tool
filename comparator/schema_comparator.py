from typing import Any, Dict, List, Optional

from db.metadata import MetadataReader


class SchemaComparator:
    """Compara metadata entre BD ORIGEN y BD DESTINO."""

    def __init__(self, source_reader: MetadataReader, target_reader: MetadataReader) -> None:
        self.source_reader = source_reader
        self.target_reader = target_reader

    def compare(self) -> List[Dict[str, Any]]:
        source = self.source_reader.read()
        target = self.target_reader.read()
        differences: List[Dict[str, Any]] = []

        differences.extend(self._compare_schema(source, target))
        differences.extend(self._compare_extensions(source, target))
        differences.extend(self._compare_functions(source, target))
        differences.extend(self._compare_sequences(source, target))
        differences.extend(self._compare_tables(source, target))
        differences.extend(self._compare_columns(source, target))
        differences.extend(self._compare_constraints(source, target))
        differences.extend(self._compare_indexes(source, target))
        differences.extend(self._compare_views(source, target))
        differences.extend(self._compare_materialized_views(source, target))
        differences.extend(self._compare_triggers(source, target))

        return differences

    def _compare_schema(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        if target["schemas"]:
            return []

        schema = source["schema"]
        return [
            self._difference(
                "SCHEMA_MISSING",
                f"El esquema '{schema}' existe en ORIGEN y no existe en DESTINO.",
                f"CREATE SCHEMA IF NOT EXISTS {self._q(schema)};",
                "ALTO",
                "El esquema ausente puede impedir crear o resolver objetos dependientes.",
            )
        ]

    def _compare_tables(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for table_name in sorted(source["tables"]):
            if table_name in target["tables"]:
                continue

            columns = source["columns"].get(table_name, {})
            column_defs = [
                (
                    f"    {self._q(column_name)} {self._column_type(column)}"
                    f"{self._generated_sql(column)}"
                    f"{self._default_sql(column) if not self._generated_sql(column) else ''}"
                    f"{self._identity_sql(column)}"
                    f"{self._null_sql(column)}"
                )
                for column_name, column in sorted(columns.items(), key=lambda item: item[1]["ordinal_position"])
            ]
            sql = (
                f"CREATE TABLE {self._qualified(source['schema'], table_name)} (\n"
                + ",\n".join(column_defs)
                + "\n);"
            )
            differences.append(
                self._difference(
                    "TABLE_MISSING",
                    f"La tabla '{source['schema']}.{table_name}' existe en ORIGEN y no existe en DESTINO.",
                    sql,
                    "CRITICA",
                    "La tabla ausente impide almacenar o consultar datos esperados por la aplicacion.",
                )
            )
        return differences

    def _compare_columns(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for table_name, source_columns in sorted(source["columns"].items()):
            if table_name not in target["tables"]:
                continue

            target_columns = target["columns"].get(table_name, {})
            for column_name, source_column in sorted(
                source_columns.items(), key=lambda item: item[1]["ordinal_position"]
            ):
                target_column = target_columns.get(column_name)
                if target_column is None:
                    sql = (
                        f"ALTER TABLE {self._qualified(target['schema'], table_name)} "
                        f"ADD COLUMN {self._q(column_name)} {self._column_type(source_column)}"
                        f"{self._generated_sql(source_column)}"
                        f"{self._default_sql(source_column) if not self._generated_sql(source_column) else ''}"
                        f"{self._identity_sql(source_column)}"
                        f"{self._null_sql(source_column)};"
                    )
                    differences.append(
                        self._difference(
                            "COLUMN_MISSING",
                            f"La columna '{table_name}.{column_name}' existe en ORIGEN y no existe en DESTINO.",
                            sql,
                            self._missing_column_impact(source_column),
                            self._missing_column_reason(source_column),
                        )
                    )
                    continue

                if self._column_signature(source_column) != self._column_signature(target_column):
                    sql = self._alter_column_sql(target["schema"], table_name, column_name, source_column)
                    impact = "ALTO"
                    reason = "La definicion completa de la columna no coincide con ORIGEN."
                    if self._is_generated_column_difference(source_column, target_column):
                        impact = "CRITICA"
                        reason = (
                            "La columna generada falta o esta mal definida. PostgreSQL no permite "
                            "convertir una columna normal a generated directamente."
                        )
                        sql = self._recreate_generated_column_sql(
                            target,
                            table_name,
                            column_name,
                            source_column,
                        )
                    differences.append(
                        self._difference(
                            "COLUMN_DIFFERENT",
                            f"La columna '{table_name}.{column_name}' tiene definicion distinta.",
                            sql,
                            impact,
                            reason,
                            str(self._column_signature(source_column)),
                            str(self._column_signature(target_column)),
                        )
                    )

        return differences

    def _compare_constraints(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        target_by_signature = {
            self._constraint_signature(constraint): constraint
            for constraint in target["constraints"].values()
        }
        for _key, source_constraint in sorted(source["constraints"].items()):
            if source_constraint["constraint_type"] == "n":
                continue
            signature = self._constraint_signature(source_constraint)
            target_constraint = target_by_signature.get(signature)
            if target_constraint:
                continue

            equivalent_index = self._equivalent_unique_index(source_constraint, target)
            if equivalent_index:
                continue

            add_sql = (
                f"ALTER TABLE {self._qualified(target['schema'], source_constraint['table_name'])} "
                f"ADD CONSTRAINT {self._q(source_constraint['constraint_name'])} "
                f"{source_constraint['definition']};"
            )
            differences.append(
                self._difference(
                    "CONSTRAINT_MISSING_OR_DIFFERENT",
                    (
                        f"La constraint equivalente a '{source_constraint['constraint_name']}' "
                        "no existe o es diferente en DESTINO."
                    ),
                    add_sql,
                    self._constraint_impact(source_constraint),
                    self._constraint_reason(source_constraint),
                    source_constraint.get("definition", ""),
                    "",
                )
            )
        return differences

    def _compare_indexes(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        constraint_names = {
            constraint["constraint_name"]
            for constraint in source["constraints"].values()
        }
        target_signatures = {
            self._index_signature(index): index
            for index in target["indexes"].values()
        }

        for _key, source_index in sorted(source["indexes"].items()):
            if source_index["index_name"] in constraint_names:
                continue

            signature = self._index_signature(source_index)
            target_index = target_signatures.get(signature)
            if target_index:
                continue

            differences.append(
                self._difference(
                    "INDEX_MISSING_OR_DIFFERENT",
                    f"El indice equivalente a '{source_index['index_name']}' no existe o es diferente en DESTINO.",
                    self._index_sql(target["schema"], source_index, None),
                    "ALTO" if source_index.get("expressions") else "MEDIO",
                    "El indice ausente o diferente puede afectar rendimiento y planes de consulta.",
                    source_index.get("indexdef", ""),
                    target_index.get("indexdef", "") if target_index else "",
                )
            )
        return differences

    def _compare_sequences(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for sequence_name, source_sequence in sorted(source["sequences"].items()):
            target_sequence = target["sequences"].get(sequence_name)
            if target_sequence and self._sequence_signature(source_sequence) == self._sequence_signature(target_sequence):
                continue

            sql = self._sequence_sql(target["schema"], source_sequence, target_sequence)
            differences.append(
                self._difference(
                    "SEQUENCE_MISSING_OR_DIFFERENT",
                    f"La secuencia '{sequence_name}' no existe o es diferente en DESTINO.",
                    sql,
                    "ALTO",
                    "La secuencia ausente puede romper inserciones que dependen de valores generados.",
                    str(source_sequence),
                    str(target_sequence or ""),
                )
            )
        return differences

    def _compare_views(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for view_name, source_view in sorted(source["views"].items()):
            target_view = target["views"].get(view_name)
            if target_view and self._normalize_sql(target_view["view_definition"]) == self._normalize_sql(source_view["view_definition"]):
                continue

            action = "no existe" if target_view is None else "es diferente"
            sql = (
                f"CREATE OR REPLACE VIEW {self._qualified(target['schema'], view_name)} AS\n"
                f"{source_view['view_definition'].strip()};"
            )
            differences.append(
                self._difference(
                    "VIEW_MISSING_OR_DIFFERENT",
                    f"La vista '{view_name}' {action} en DESTINO.",
                    sql,
                    "MEDIO",
                    "La vista ausente o diferente puede afectar consultas, reportes o integraciones.",
                    source_view.get("view_definition", ""),
                    target_view.get("view_definition", "") if target_view else "",
                )
            )
        return differences

    def _compare_materialized_views(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for view_name, source_view in sorted(source.get("materialized_views", {}).items()):
            target_view = target.get("materialized_views", {}).get(view_name)
            if target_view and self._normalize_sql(target_view["view_definition"]) == self._normalize_sql(source_view["view_definition"]):
                continue

            sql = (
                f"DROP MATERIALIZED VIEW IF EXISTS {self._qualified(target['schema'], view_name)};\n"
                f"CREATE MATERIALIZED VIEW {self._qualified(target['schema'], view_name)} AS\n"
                f"{source_view['view_definition'].strip()};"
            )
            differences.append(
                self._difference(
                    "MATERIALIZED_VIEW_MISSING_OR_DIFFERENT",
                    f"La vista materializada '{view_name}' no existe o es diferente en DESTINO.",
                    sql,
                    "ALTO",
                    "La vista materializada ausente o diferente cambia datos derivados y consultas.",
                    source_view.get("view_definition", ""),
                    target_view.get("view_definition", "") if target_view else "",
                )
            )
        return differences

    def _compare_extensions(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        target_extensions = target.get("extensions", {})
        needed_extensions = set(source.get("extensions", {})) | self._infer_required_extensions(source)
        for extension_name in sorted(needed_extensions):
            if extension_name in target_extensions:
                continue
            differences.append(
                self._difference(
                    "EXTENSION_MISSING",
                    f"La extension '{extension_name}' es necesaria y no existe en DESTINO.",
                    f"CREATE EXTENSION IF NOT EXISTS {self._q(extension_name)};",
                    "ALTO",
                    "La extension faltante puede romper funciones, opclasses o DDL dependiente.",
                    extension_name,
                    "",
                )
            )
        return differences

    def _compare_functions(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        target_functions = target.get("functions", {})
        for key, source_function in sorted(source.get("functions", {}).items()):
            target_function = target_functions.get(key)
            if target_function and self._function_signature(source_function) == self._function_signature(target_function):
                continue
            differences.append(
                self._difference(
                    "FUNCTION_MISSING_OR_DIFFERENT",
                    f"La funcion '{key}' no existe o es diferente en DESTINO.",
                    source_function["definition"].rstrip() + ";",
                    "ALTO",
                    "La funcion faltante o diferente puede romper defaults, columnas generadas, constraints, indices, triggers o vistas.",
                    source_function.get("definition", ""),
                    target_function.get("definition", "") if target_function else "",
                )
            )
        return differences

    def _compare_triggers(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        target_triggers = {
            self._trigger_signature(trigger): trigger
            for trigger in target.get("triggers", {}).values()
        }
        for _key, source_trigger in sorted(source.get("triggers", {}).items()):
            signature = self._trigger_signature(source_trigger)
            if signature in target_triggers:
                continue
            differences.append(
                self._difference(
                    "TRIGGER_MISSING_OR_DIFFERENT",
                    f"El trigger '{source_trigger['table_name']}.{source_trigger['trigger_name']}' no existe o es diferente en DESTINO.",
                    (
                        f"DROP TRIGGER IF EXISTS {self._q(source_trigger['trigger_name'])} "
                        f"ON {self._qualified(target['schema'], source_trigger['table_name'])};\n"
                        f"{source_trigger['definition']};"
                    ),
                    "ALTO",
                    "El trigger faltante o diferente cambia comportamiento de escritura.",
                    source_trigger.get("definition", ""),
                    "",
                )
            )
        return differences

    def _alter_column_sql(
        self, schema: str, table_name: str, column_name: str, source_column: Dict[str, Any]
    ) -> str:
        table = self._qualified(schema, table_name)
        column = self._q(column_name)
        statements = [
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {self._column_type(source_column)} USING {column}::{self._column_type(source_column)};",
        ]

        if source_column["column_default"] is None:
            statements.append(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT;")
        else:
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {source_column['column_default']};"
            )

        if source_column["is_nullable"] == "NO":
            statements.append(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL;")
        else:
            statements.append(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL;")

        if source_column.get("comment"):
            statements.append(
                f"COMMENT ON COLUMN {table}.{column} IS {self._literal(source_column['comment'])};"
            )

        return "\n".join(statements)

    def _column_signature(self, column: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            self._column_type(column),
            column["is_nullable"],
            self._normalize_sql(column.get("column_default")),
            column.get("identity_kind") or "",
            column.get("identity_generation") or "",
            column.get("generated_kind") or "",
            self._normalize_sql(column.get("generation_expression")),
            column.get("collation_name") or "",
            column.get("comment") or "",
        )

    def _column_type(self, column: Dict[str, Any]) -> str:
        if column.get("formatted_type"):
            return column["formatted_type"]

        data_type = column["data_type"]
        udt_name = column["udt_name"]

        if data_type == "character varying":
            return f"VARCHAR({column['character_maximum_length']})"
        if data_type == "character":
            return f"CHAR({column['character_maximum_length']})"
        if data_type == "numeric":
            precision = column["numeric_precision"]
            scale = column["numeric_scale"]
            return f"NUMERIC({precision},{scale})" if precision is not None else "NUMERIC"
        if data_type == "timestamp without time zone":
            return "TIMESTAMP"
        if data_type == "timestamp with time zone":
            return "TIMESTAMPTZ"
        if data_type == "USER-DEFINED":
            return self._q(udt_name)
        if data_type == "ARRAY":
            return f"{udt_name.replace('_', '', 1)}[]"

        return data_type.upper()

    def _index_sql(
        self,
        target_schema: str,
        source_index: Dict[str, Any],
        target_index: Optional[Dict[str, Any]],
    ) -> str:
        create_sql = source_index["indexdef"].rstrip(";")
        source_schema = source_index["schema_name"]
        if source_schema != target_schema:
            create_sql = create_sql.replace(
                f"{self._q(source_schema)}.",
                f"{self._q(target_schema)}.",
            )

        if target_index is None:
            return f"{create_sql};"

        return (
            f"DROP INDEX {self._qualified(target_schema, source_index['index_name'])};\n"
            f"{create_sql};"
        )

    def _default_sql(self, column: Dict[str, Any]) -> str:
        if column["column_default"] is None:
            return ""
        return f" DEFAULT {column['column_default']}"

    def _null_sql(self, column: Dict[str, Any]) -> str:
        return " NOT NULL" if column["is_nullable"] == "NO" else ""

    def _generated_sql(self, column: Dict[str, Any]) -> str:
        if column.get("generated_kind") == "s" and column.get("generation_expression"):
            return f" GENERATED ALWAYS AS ({column['generation_expression']}) STORED"
        return ""

    def _identity_sql(self, column: Dict[str, Any]) -> str:
        generation = column.get("identity_generation")
        if generation:
            return f" GENERATED {generation} AS IDENTITY"
        return ""

    def _missing_column_impact(self, column: Dict[str, Any]) -> str:
        if column["is_nullable"] == "NO" and column["column_default"] is None:
            return "CRITICA"
        if column["is_nullable"] == "NO" or column["column_default"] is not None:
            return "ALTO"
        return "MEDIO"

    def _missing_column_reason(self, column: Dict[str, Any]) -> str:
        if column["is_nullable"] == "NO" and column["column_default"] is None:
            return "La columna faltante es obligatoria y no tiene default; puede bloquear cargas o dejar datos incompletos."
        if column["is_nullable"] == "NO":
            return "La columna faltante es obligatoria; puede impactar reglas de escritura o consistencia."
        if column["column_default"] is not None:
            return "La columna faltante tiene default; puede cambiar comportamiento de inserciones."
        return "La columna faltante es nullable y no tiene default; el impacto suele ser funcional, no de integridad inmediata."

    def _constraint_impact(self, constraint: Dict[str, Any]) -> str:
        constraint_type = constraint["constraint_type"]
        if constraint_type in {"p", "f", "u"}:
            return "CRITICA"
        if constraint_type == "c":
            return "ALTO"
        return "ALTO"

    def _constraint_reason(self, constraint: Dict[str, Any]) -> str:
        reasons = {
            "p": "La llave primaria protege identidad e integridad de filas.",
            "f": "La llave foranea protege integridad referencial entre tablas.",
            "u": "La constraint UNIQUE evita duplicados que comprometen reglas de negocio.",
            "c": "La constraint CHECK protege reglas de dominio de datos.",
        }
        return reasons.get(
            constraint["constraint_type"],
            "La constraint ausente o diferente puede comprometer reglas de integridad.",
        )

    def _is_generated_column_difference(
        self,
        source_column: Dict[str, Any],
        target_column: Dict[str, Any],
    ) -> bool:
        return (
            (source_column.get("generated_kind") or "") != (target_column.get("generated_kind") or "")
            or self._normalize_sql(source_column.get("generation_expression"))
            != self._normalize_sql(target_column.get("generation_expression"))
        ) and source_column.get("generated_kind") == "s"

    def _recreate_generated_column_sql(
        self,
        target: Dict[str, Any],
        table_name: str,
        column_name: str,
        source_column: Dict[str, Any],
    ) -> str:
        table = self._qualified(target["schema"], table_name)
        column = self._q(column_name)
        dependent_indexes = [
            index
            for index in target.get("indexes", {}).values()
            if index["table_name"] == table_name
            and (
                column_name in (index.get("columns") or [])
                or column_name.lower() in (index.get("indexdef") or "").lower()
            )
        ]
        statements = [
            f"-- ADVERTENCIA: recrear una columna generada implica perder/recalcular los valores de {table}.{column}.",
            "-- Pre-check: revisa dependencias antes de ejecutar en produccion.",
        ]
        for index in dependent_indexes:
            statements.append(
                f"DROP INDEX IF EXISTS {self._qualified(target['schema'], index['index_name'])};"
            )
        statements.extend(
            [
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column};",
                (
                    f"ALTER TABLE {table} ADD COLUMN {column} {self._column_type(source_column)}"
                    f"{self._generated_sql(source_column)}{self._null_sql(source_column)};"
                ),
            ]
        )
        for index in dependent_indexes:
            statements.append(f"{index['indexdef'].rstrip(';')};")
        return "\n".join(statements)

    def _constraint_signature(self, constraint: Dict[str, Any]) -> tuple[Any, ...]:
        constraint_type = constraint["constraint_type"]
        if constraint_type == "f":
            return (
                constraint_type,
                tuple(constraint.get("columns") or []),
                constraint.get("referenced_schema") or "",
                constraint.get("referenced_table") or "",
                tuple(constraint.get("referenced_columns") or []),
                constraint.get("on_update") or "",
                constraint.get("on_delete") or "",
                bool(constraint.get("deferrable")),
                bool(constraint.get("initially_deferred")),
            )
        if constraint_type in {"p", "u"}:
            return (constraint_type, tuple(constraint.get("columns") or []))
        return (
            constraint_type,
            self._normalize_sql(constraint.get("definition")),
            bool(constraint.get("deferrable")),
            bool(constraint.get("initially_deferred")),
        )

    def _equivalent_unique_index(
        self,
        constraint: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if constraint["constraint_type"] not in {"p", "u"}:
            return None
        source_columns = tuple(constraint.get("columns") or [])
        for index in target.get("indexes", {}).values():
            if index["table_name"] != constraint["table_name"]:
                continue
            if not index.get("is_unique"):
                continue
            if tuple(index.get("columns") or []) == source_columns and not index.get("predicate"):
                return index
        return None

    def _index_signature(self, index: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            index.get("table_name"),
            index.get("method"),
            bool(index.get("is_unique")),
            tuple(index.get("columns") or []),
            self._normalize_sql(index.get("expressions")),
            self._normalize_sql(index.get("predicate")),
            tuple(index.get("opclasses") or []),
            self._normalize_sql(index.get("indexdef")),
        )

    def _sequence_signature(self, sequence: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            sequence.get("data_type"),
            str(sequence.get("start_value")),
            str(sequence.get("increment")),
            str(sequence.get("minimum_value")),
            str(sequence.get("maximum_value")),
            str(sequence.get("cache_size")),
            bool(sequence.get("cycle")),
            sequence.get("owner_table") or "",
            sequence.get("owner_column") or "",
        )

    def _sequence_sql(
        self,
        target_schema: str,
        source_sequence: Dict[str, Any],
        target_sequence: Optional[Dict[str, Any]],
    ) -> str:
        name = source_sequence["sequence_name"]
        qualified = self._qualified(target_schema, name)
        if target_sequence is None:
            statements = [
                (
                    f"CREATE SEQUENCE IF NOT EXISTS {qualified} "
                    f"AS {source_sequence['data_type']} "
                    f"START WITH {source_sequence['start_value']} "
                    f"INCREMENT BY {source_sequence['increment']} "
                    f"MINVALUE {source_sequence['minimum_value']} "
                    f"MAXVALUE {source_sequence['maximum_value']} "
                    f"CACHE {source_sequence['cache_size']} "
                    f"{'CYCLE' if source_sequence['cycle'] else 'NO CYCLE'};"
                )
            ]
        else:
            statements = [
                (
                    f"ALTER SEQUENCE {qualified} "
                    f"INCREMENT BY {source_sequence['increment']} "
                    f"MINVALUE {source_sequence['minimum_value']} "
                    f"MAXVALUE {source_sequence['maximum_value']} "
                    f"CACHE {source_sequence['cache_size']} "
                    f"{'CYCLE' if source_sequence['cycle'] else 'NO CYCLE'};"
                )
            ]
        if source_sequence.get("owner_table") and source_sequence.get("owner_column"):
            statements.append(
                f"ALTER SEQUENCE {qualified} OWNED BY "
                f"{self._qualified(target_schema, source_sequence['owner_table'])}.{self._q(source_sequence['owner_column'])};"
            )
        return "\n".join(statements)

    def _function_signature(self, function: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            function.get("return_type"),
            function.get("language"),
            self._normalize_sql(function.get("definition")),
            function.get("volatility"),
            bool(function.get("security_definer")),
            bool(function.get("strict_null_input")),
        )

    def _trigger_signature(self, trigger: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            trigger.get("table_name"),
            self._normalize_sql(trigger.get("definition")),
            trigger.get("enabled"),
            trigger.get("function_schema"),
            trigger.get("function_name"),
            trigger.get("function_arguments"),
        )

    def _infer_required_extensions(self, metadata: Dict[str, Any]) -> set[str]:
        ddl_text = "\n".join(
            str(value)
            for collection_name in ("columns", "constraints", "indexes", "views", "materialized_views", "triggers", "functions")
            for value in self._flatten_metadata_collection(metadata.get(collection_name, {}))
        ).lower()
        required = set()
        if "gin_trgm_ops" in ddl_text or "similarity(" in ddl_text:
            required.add("pg_trgm")
        if "soundex(" in ddl_text or "dmetaphone" in ddl_text:
            required.add("fuzzystrmatch")
        return required

    def _flatten_metadata_collection(self, collection: Dict[str, Any]) -> List[Any]:
        values = []
        for value in collection.values():
            if isinstance(value, dict):
                values.extend(value.values())
            else:
                values.append(value)
        return values

    def _normalize_sql(self, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).strip().rstrip(";").split()).lower()

    def _literal(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _difference(
        self,
        difference_type: str,
        description: str,
        sql: str,
        impact: str,
        impact_reason: str,
        source_definition: str = "",
        target_definition: str = "",
    ) -> Dict[str, Any]:
        return {
            "type": difference_type,
            "impact": impact,
            "impact_reason": impact_reason,
            "description": description,
            "sql": sql,
            "source_definition": source_definition,
            "target_definition": target_definition,
        }

    def _qualified(self, schema: str, name: str) -> str:
        return f"{self._q(schema)}.{self._q(name)}"

    def _q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'
