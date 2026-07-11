from pathlib import Path
from typing import Any, List, Tuple

from comparator.table_comparator import TableComparisonResult
from generator.pdf_report import SimplePDFReport


def table_comparison_lines(result: TableComparisonResult) -> List[str]:
    lines = [
        "REPORTE DE COMPARACION DE TABLA",
        f"Fecha: {result.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Tabla solicitada: {result.table_name}",
        f"Comparando: {result.source_name} -> {result.target_name}",
        f"{result.source_name}: {result.source_schema}.{result.source_table_name}",
        f"{result.target_name}: {result.target_schema}.{result.target_table_name}",
        "",
        f"Estado general: {'IGUAL' if result.table_equal else 'DIFERENTE'}",
        f"Estructura: {'IGUAL' if result.structure_equal else 'DIFERENTE'}",
        f"Datos: {data_status(result)}",
        "",
        "Resumen de datos:",
        f"  Filas {result.source_name}: {result.source_row_count}",
        f"  Filas {result.target_name}: {result.target_row_count}",
    ]
    if result.data_compared:
        lines.extend(
            [
                f"  Filas solo en {result.source_name}: {result.only_source_count}",
                f"  Filas solo en {result.target_name}: {result.only_target_count}",
                f"  Columnas comparadas: {', '.join(result.compared_columns)}",
            ]
        )
    else:
        lines.append(f"  Datos no comparados: {result.data_skip_reason}")

    lines.extend(["", "Detalle de estructura:"])
    if result.structure_equal:
        lines.append("  No se encontraron diferencias de estructura.")
    else:
        for index, difference in enumerate(result.structure_differences, start=1):
            lines.append(
                f"  {index}. [{difference['impact']}] {difference['type']}: "
                f"{difference['description']}"
            )

    if result.data_compared and not result.data_equal:
        lines.extend(["", f"Muestras de filas solo en {result.source_name}:"])
        lines.extend(sample_lines(result.only_source_samples))
        lines.extend(["", f"Muestras de filas solo en {result.target_name}:"])
        lines.extend(sample_lines(result.only_target_samples))

    lines.extend(
        [
            "",
            f"Schema SQL en {result.source_name}:",
            result.source_schema_sql,
            "",
            f"Schema SQL en {result.target_name}:",
            result.target_schema_sql,
        ]
    )
    return lines


def data_status(result: TableComparisonResult) -> str:
    if not result.data_compared:
        return "NO COMPARADOS"
    return "IGUALES" if result.data_equal else "DIFERENTES"


def sample_lines(samples: List[Tuple[Any, ...]]) -> List[str]:
    if not samples:
        return ["  Sin muestras."]
    return [f"  {index}. {sample}" for index, sample in enumerate(samples, start=1)]


def write_table_comparison_report(
    result: TableComparisonResult,
    output_dir: str = "output",
) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    txt_path = output_path / "reporte_comparacion_tabla.txt"
    pdf_path = output_path / "reporte_comparacion_tabla.pdf"
    lines = table_comparison_lines(result)
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    SimplePDFReport(str(pdf_path)).write(lines)
    return txt_path, pdf_path
