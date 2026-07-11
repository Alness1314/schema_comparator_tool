from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from db.table_data_cleaner import TableRef
from db.table_dropper import DropTablesPlan
from generator.pdf_report import SimplePDFReport


def drop_tables_plan_lines(plan: DropTablesPlan, connection_name: str) -> List[str]:
    lines = [
        "INFORME PREVIO DE ELIMINACION DE TABLAS",
        f"Fecha de analisis: {plan.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Conexion seleccionada: {connection_name}",
        f"Motor: {plan.engine}",
        f"Base de datos: {plan.database}",
        f"Tabla solicitada: {plan.target.qualified_name}",
        "",
        f"Tablas que se eliminaran: {len(plan.drop_order)}",
        f"Filas contenidas en esas tablas: {plan.total_rows}",
        "",
        "Orden de eliminacion:",
    ]
    for index, table in enumerate(plan.drop_order, start=1):
        lines.append(
            f"  {index}. {table.qualified_name}: {plan.row_counts[table]} fila(s)"
        )
    lines.extend(
        [
            "",
            "ATENCION: esta operacion elimina las tablas completas, no solo sus datos.",
            "Se intentara ejecutar dentro de una transaccion cuando el motor lo permita.",
        ]
    )
    return lines


def write_drop_tables_report(
    plan: DropTablesPlan,
    dropped_tables: List[TableRef],
    connection_name: str,
    output_dir: str = "output",
) -> Tuple[Path, Path]:
    timestamp = datetime.now()
    output_path = Path(output_dir)
    txt_path = output_path / "reporte_eliminacion_tablas.txt"
    pdf_path = output_path / "reporte_eliminacion_tablas.pdf"
    output_path.mkdir(parents=True, exist_ok=True)

    lines = [
        "REPORTE FINAL DE ELIMINACION DE TABLAS",
        f"Fecha de ejecucion: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        "Estado: COMPLETADO",
        f"Conexion seleccionada: {connection_name}",
        f"Motor: {plan.engine}",
        f"Base de datos: {plan.database}",
        f"Tabla solicitada: {plan.target.qualified_name}",
        "",
        f"Tablas eliminadas: {len(dropped_tables)}",
        "",
        "Detalle:",
    ]
    for index, table in enumerate(dropped_tables, start=1):
        lines.append(f"  {index}. {table.qualified_name}")
    lines.extend(
        [
            "",
            "La operacion fue confirmada por el usuario antes de ejecutarse.",
        ]
    )

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    SimplePDFReport(str(pdf_path)).write(lines)
    return txt_path, pdf_path
