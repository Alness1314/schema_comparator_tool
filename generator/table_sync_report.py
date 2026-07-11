from pathlib import Path
from typing import List

from comparator.table_comparator import TableSyncResult
from generator.pdf_report import SimplePDFReport


def table_sync_lines(result: TableSyncResult) -> List[str]:
    return [
        "REPORTE DE IGUALACION DE TABLA",
        f"Fecha: {result.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Tipo: {result.sync_type}",
        f"Tabla: {result.table_name}",
        f"Direccion: {result.source_name} -> {result.target_name}",
        f"Estado: {result.status}",
        "",
        f"Sentencias ejecutadas: {result.statements_executed}",
        f"Filas copiadas: {result.rows_copied}",
        f"Detalle: {result.detail}",
    ]


def write_table_sync_report(
    result: TableSyncResult,
    output_dir: str = "output",
) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    suffix = result.sync_type.lower()
    txt_path = output_path / f"reporte_igualacion_tabla_{suffix}.txt"
    pdf_path = output_path / f"reporte_igualacion_tabla_{suffix}.pdf"
    lines = table_sync_lines(result)
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    SimplePDFReport(str(pdf_path)).write(lines)
    return txt_path, pdf_path
