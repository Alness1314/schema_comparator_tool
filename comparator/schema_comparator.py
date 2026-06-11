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
        differences.extend(self._compare_sequences(source, target))
        differences.extend(self._compare_tables(source, target))
        differences.extend(self._compare_columns(source, target))
        differences.extend(self._compare_constraints(source, target))
        differences.extend(self._compare_indexes(source, target))
        differences.extend(self._compare_views(source, target))

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
                f"    {self._q(column_name)} {self._column_type(column)}{self._null_sql(column)}"
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
                        f"{self._default_sql(source_column)}{self._null_sql(source_column)};"
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
                    differences.append(
                        self._difference(
                            "COLUMN_DIFFERENT",
                            f"La columna '{table_name}.{column_name}' tiene definicion distinta.",
                            sql,
                            "BAJO",
                            "La columna existe en ambos lados; la diferencia es de definicion tecnica.",
                        )
                    )

        return differences

    def _compare_constraints(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for key, source_constraint in sorted(source["constraints"].items()):
            target_constraint = target["constraints"].get(key)
            if target_constraint and target_constraint["definition"] == source_constraint["definition"]:
                continue

            action = "no existe" if target_constraint is None else "es diferente"
            add_sql = (
                f"ALTER TABLE {self._qualified(target['schema'], source_constraint['table_name'])} "
                f"ADD CONSTRAINT {self._q(source_constraint['constraint_name'])} "
                f"{source_constraint['definition']};"
            )
            if target_constraint is None:
                sql = add_sql
            else:
                sql = (
                    f"ALTER TABLE {self._qualified(target['schema'], source_constraint['table_name'])} "
                    f"DROP CONSTRAINT {self._q(source_constraint['constraint_name'])};\n"
                    f"{add_sql}"
                )
            differences.append(
                self._difference(
                    "CONSTRAINT_MISSING_OR_DIFFERENT",
                    f"La constraint '{key}' {action} en DESTINO.",
                    sql,
                    self._constraint_impact(source_constraint),
                    self._constraint_reason(source_constraint),
                )
            )
        return differences

    def _compare_indexes(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        constraint_names = {
            constraint["constraint_name"]
            for constraint in source["constraints"].values()
        }

        for key, source_index in sorted(source["indexes"].items()):
            if source_index["index_name"] in constraint_names:
                continue

            target_index = target["indexes"].get(key)
            if target_index and target_index["indexdef"] == source_index["indexdef"]:
                continue

            action = "no existe" if target_index is None else "es diferente"
            differences.append(
                self._difference(
                    "INDEX_MISSING_OR_DIFFERENT",
                    f"El indice '{key}' {action} en DESTINO.",
                    self._index_sql(target["schema"], source_index, target_index),
                    "MEDIO",
                    "El indice ausente o diferente puede afectar rendimiento y planes de consulta.",
                )
            )
        return differences

    def _compare_sequences(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for sequence_name, source_sequence in sorted(source["sequences"].items()):
            if sequence_name in target["sequences"]:
                continue

            sql = (
                f"CREATE SEQUENCE {self._qualified(target['schema'], sequence_name)} "
                f"AS {source_sequence['data_type']} "
                f"INCREMENT BY {source_sequence['increment']} "
                f"MINVALUE {source_sequence['minimum_value']} "
                f"MAXVALUE {source_sequence['maximum_value']} "
                f"START WITH {source_sequence['start_value']};"
            )
            differences.append(
                self._difference(
                    "SEQUENCE_MISSING",
                    f"La secuencia '{sequence_name}' existe en ORIGEN y no existe en DESTINO.",
                    sql,
                    "ALTO",
                    "La secuencia ausente puede romper inserciones que dependen de valores generados.",
                )
            )
        return differences

    def _compare_views(self, source: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
        differences = []
        for view_name, source_view in sorted(source["views"].items()):
            target_view = target["views"].get(view_name)
            if target_view and target_view["view_definition"] == source_view["view_definition"]:
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
                )
            )
        return differences

    def _alter_column_sql(
        self, schema: str, table_name: str, column_name: str, source_column: Dict[str, Any]
    ) -> str:
        table = self._qualified(schema, table_name)
        column = self._q(column_name)
        statements = [
            (
                f"ALTER TABLE {table} ALTER COLUMN {column} "
                f"TYPE {self._column_type(source_column)} USING {column}::{self._column_type(source_column)};"
            ),
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

        return "\n".join(statements)

    def _column_signature(self, column: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            self._column_type(column),
            column["is_nullable"],
            column["column_default"],
        )

    def _column_type(self, column: Dict[str, Any]) -> str:
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

    def _difference(
        self,
        difference_type: str,
        description: str,
        sql: str,
        impact: str,
        impact_reason: str,
    ) -> Dict[str, Any]:
        return {
            "type": difference_type,
            "impact": impact,
            "impact_reason": impact_reason,
            "description": description,
            "sql": sql,
        }

    def _qualified(self, schema: str, name: str) -> str:
        return f"{self._q(schema)}.{self._q(name)}"

    def _q(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'
