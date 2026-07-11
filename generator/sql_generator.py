from pathlib import Path
from typing import Any, Dict, List


class SQLGenerator:
    """Genera scripts SQL a partir de diferencias detectadas."""

    def __init__(self, output_path: str = "output/scripts.sql") -> None:
        self.output_path = Path(output_path)

    def write(self, differences: List[Dict[str, Any]]) -> None:
        """Escribe sentencias SQL en el archivo de salida."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        statements = [
            "-- Scripts SQL generados por db-compare-tool.",
            "-- Objetivo: aplicar en BD DESTINO para acercarla a BD ORIGEN.",
            "-- Revisa el script antes de ejecutarlo en un ambiente real.",
            "-- Evita DROP TABLE CASCADE; los cambios destructivos incluyen advertencias.",
            "-- Ejecuta primero en ambiente de pruebas y con backup.",
            "",
        ]

        if not differences:
            statements.append("-- No se encontraron diferencias de metadata.")
        else:
            for index, difference in enumerate(differences, start=1):
                statements.extend(
                    [
                        f"-- {index}. {difference['type']}",
                        f"-- Impacto: {difference['impact']}",
                        f"-- Motivo: {difference['impact_reason']}",
                        f"-- {difference['description']}",
                        "-- Definicion ORIGEN:",
                        self._comment_block(difference.get("source_definition", "")),
                        "-- Definicion DESTINO:",
                        self._comment_block(difference.get("target_definition", "")),
                        "-- Cambio sugerido:",
                        difference["sql"].rstrip(),
                        "",
                    ]
                )

        self.output_path.write_text("\n".join(statements), encoding="utf-8")

    def _comment_block(self, text: str) -> str:
        if not text:
            return "-- (no existe o no disponible)"
        return "\n".join(f"-- {line}" for line in str(text).splitlines())
