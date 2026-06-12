from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from db.table_data_cleaner import DeletionPlan, TableRef
from generator.pdf_report import SimplePDFReport


def deletion_plan_lines(plan: DeletionPlan, connection_name: str) -> List[str]:
    lines = [
        "INFORME PREVIO DE BORRADO DE DATOS",
        f"Fecha de analisis: {plan.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Conexion seleccionada: {connection_name}",
        f"Motor: {plan.engine}",
        f"Base de datos: {plan.database}",
        f"Tabla solicitada: {plan.target.qualified_name}",
        "",
        f"Tablas que se vaciaran: {len(plan.deletion_order)}",
        f"Filas detectadas: {plan.total_rows}",
        "",
        "Orden de borrado:",
    ]
    for index, table in enumerate(plan.deletion_order, start=1):
        lines.append(
            f"  {index}. {table.qualified_name}: {plan.row_counts[table]} fila(s)"
        )
    lines.extend(
        [
            "",
            "El borrado se ejecutara en una sola transaccion.",
            "Si una operacion falla, se revertiran todos los cambios.",
        ]
    )
    return lines


def write_deletion_report(
    plan: DeletionPlan,
    deleted_rows: Dict[TableRef, int],
    connection_name: str,
    output_dir: str = "output",
) -> Tuple[Path, Path]:
    timestamp = datetime.now()
    output_path = Path(output_dir)
    txt_path = output_path / "reporte_borrado_datos.txt"
    pdf_path = output_path / "reporte_borrado_datos.pdf"
    output_path.mkdir(parents=True, exist_ok=True)

    lines = [
        "REPORTE FINAL DE BORRADO DE DATOS",
        f"Fecha de ejecucion: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        "Estado: COMPLETADO",
        f"Conexion seleccionada: {connection_name}",
        f"Motor: {plan.engine}",
        f"Base de datos: {plan.database}",
        f"Tabla solicitada: {plan.target.qualified_name}",
        "",
        f"Tablas procesadas: {len(plan.deletion_order)}",
        f"Total de filas borradas: {sum(deleted_rows.values())}",
        "",
        "Detalle:",
    ]
    for index, table in enumerate(plan.deletion_order, start=1):
        lines.append(
            f"  {index}. {table.qualified_name}: {deleted_rows.get(table, 0)} fila(s) borrada(s)"
        )
    lines.extend(
        [
            "",
            "Todas las operaciones fueron confirmadas en la misma transaccion.",
        ]
    )

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    SimplePDFReport(str(pdf_path)).write(lines)
    return txt_path, pdf_path
