import unittest

from comparator.schema_comparator import SchemaComparator


class RichSchemaComparatorTests(unittest.TestCase):
    def test_generated_column_difference_recreates_column_and_dependent_indexes(self) -> None:
        comparator = SchemaComparator.__new__(SchemaComparator)
        source = {
            "schema": "public",
            "tables": {"person": {}},
            "columns": {
                "person": {
                    "full_name": {
                        "data_type": "text",
                        "udt_name": "text",
                        "formatted_type": "text",
                        "is_nullable": "YES",
                        "column_default": None,
                        "generated_kind": "s",
                        "generation_expression": "first_name || ' ' || last_name",
                        "identity_kind": "",
                        "identity_generation": "",
                        "collation_name": "",
                        "comment": None,
                        "ordinal_position": 1,
                    }
                }
            },
        }
        target = {
            "schema": "public",
            "tables": {"person": {}},
            "columns": {
                "person": {
                    "full_name": {
                        "data_type": "text",
                        "udt_name": "text",
                        "formatted_type": "text",
                        "is_nullable": "YES",
                        "column_default": None,
                        "generated_kind": "",
                        "generation_expression": None,
                        "identity_kind": "",
                        "identity_generation": "",
                        "collation_name": "",
                        "comment": None,
                        "ordinal_position": 1,
                    }
                }
            },
            "indexes": {
                "person.idx_person_full_name": {
                    "table_name": "person",
                    "index_name": "idx_person_full_name",
                    "indexdef": 'CREATE INDEX idx_person_full_name ON public.person USING btree (full_name)',
                    "columns": ["full_name"],
                }
            },
        }

        differences = comparator._compare_columns(source, target)

        self.assertEqual(differences[0]["impact"], "CRITICA")
        self.assertIn("DROP INDEX IF EXISTS", differences[0]["sql"])
        self.assertIn("DROP COLUMN IF EXISTS", differences[0]["sql"])
        self.assertIn("GENERATED ALWAYS AS", differences[0]["sql"])


if __name__ == "__main__":
    unittest.main()
