from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import DB_DESTINO_CONFIG, DB_ORIGEN_CONFIG
from comparator.data_integrity_checker import DataIntegrityChecker
from comparator.schema_comparator import SchemaComparator
from db.connection import DatabaseConnection
from db.metadata import MetadataReader
from generator.pdf_report import SimplePDFReport
from generator.sql_generator import SQLGenerator


def validar_conexion(nombre: str, db: DatabaseConnection) -> Dict[str, Any]:
    """Abre, prueba y cierra una conexion de base de datos."""
    try:
        db.connect()
        resultado = db.test_connection()
        print(
            f"[OK] {nombre}: conectado a base de datos "
            f"'{resultado['database']}' en esquema '{resultado['schema']}'"
        )
        return {
            "nombre": nombre,
            "estado": "OK",
            "motor": db.parsed_url["engine"],
            "host": db.parsed_url["host"],
            "port": db.parsed_url["port"],
            "database": resultado["database"],
            "schema": resultado["schema"],
            "error": "",
        }
    except Exception as error:
        print(f"[ERROR] {nombre}: no se pudo validar la conexion. Detalle: {error}")
        return {
            "nombre": nombre,
            "estado": "ERROR",
            "motor": db.parsed_url["engine"],
            "host": db.parsed_url["host"],
            "port": db.parsed_url["port"],
            "database": db.parsed_url["database"],
            "schema": db.config.get("schema", ""),
            "error": str(error),
        }
    finally:
        db.close()


def escribir_reporte_conexiones(resultados: List[Dict[str, Any]]) -> Path:
    """Genera un reporte TXT con la validacion de conexiones."""
    output_path = Path("output/reporte_conexiones.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lineas = [
        "REPORTE DE VALIDACION DE CONEXIONES",
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for resultado in resultados:
        lineas.extend(
            [
                resultado["nombre"],
                f"  Estado: {resultado['estado']}",
                f"  Motor: {resultado['motor']}",
                f"  Host: {resultado['host']}",
                f"  Puerto: {resultado['port']}",
                f"  Base de datos: {resultado['database']}",
                f"  Esquema: {resultado['schema']}",
            ]
        )

        if resultado["error"]:
            lineas.append(f"  Error: {resultado['error']}")

        lineas.append("")

    output_path.write_text("\n".join(lineas), encoding="utf-8")
    return output_path


def comparar_metadata(origen: DatabaseConnection, destino: DatabaseConnection) -> List[Dict[str, Any]]:
    """Compara metadata entre ORIGEN y DESTINO."""
    comparator = SchemaComparator(
        MetadataReader(origen),
        MetadataReader(destino),
    )
    try:
        return comparator.compare()
    finally:
        origen.close()
        destino.close()


def revisar_integridad_datos(
    origen: DatabaseConnection,
    destino: DatabaseConnection,
) -> List[Dict[str, Any]]:
    """Revisa datos en DESTINO contra reglas de integridad esperadas por ORIGEN."""
    try:
        source_metadata = MetadataReader(origen).read()
        target_metadata = MetadataReader(destino).read()
        checker = DataIntegrityChecker(destino, source_metadata, target_metadata)
        return checker.check()
    finally:
        origen.close()
        destino.close()


def escribir_reporte_metadata(diferencias: List[Dict[str, Any]]) -> Path:
    """Genera un reporte TXT con las diferencias encontradas."""
    output_path = Path("output/reporte_metadata.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lineas = [
        "REPORTE DE COMPARACION DE METADATA",
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Objetivo: aplicar scripts en BD DESTINO para acercarla a BD ORIGEN.",
        "",
        f"Total de diferencias: {len(diferencias)}",
        "",
    ]

    if not diferencias:
        lineas.append("No se encontraron diferencias de metadata.")
    else:
        resumen: Dict[str, int] = {}
        resumen_impacto: Dict[str, int] = {}
        for diferencia in diferencias:
            resumen[diferencia["type"]] = resumen.get(diferencia["type"], 0) + 1
            resumen_impacto[diferencia["impact"]] = resumen_impacto.get(diferencia["impact"], 0) + 1

        lineas.append("Resumen por impacto:")
        for impacto in _impact_order():
            lineas.append(f"  {impacto}: {resumen_impacto.get(impacto, 0)}")
        lineas.append("")
        lineas.append("Resumen por tipo:")
        for tipo, total in sorted(resumen.items()):
            lineas.append(f"  {tipo}: {total}")
        lineas.append("")
        lineas.append("Detalle:")
        lineas.append("")

        index = 1
        for impacto in _impact_order():
            diferencias_por_impacto = [
                diferencia for diferencia in diferencias if diferencia["impact"] == impacto
            ]
            if not diferencias_por_impacto:
                continue

            lineas.append(f"IMPACTO {impacto}")
            lineas.append("")
            for diferencia in diferencias_por_impacto:
                lineas.extend(
                    [
                        f"{index}. {diferencia['type']}",
                        f"   {diferencia['description']}",
                        f"   Motivo: {diferencia['impact_reason']}",
                        "   Definicion ORIGEN:",
                        f"   {diferencia.get('source_definition', '') or '(no disponible)'}",
                        "   Definicion DESTINO:",
                        f"   {diferencia.get('target_definition', '') or '(no existe o no disponible)'}",
                        "   Script sugerido:",
                        f"   {diferencia.get('sql', '').strip() or '(sin script automatico)'}",
                        "",
                    ]
                )
                index += 1

    output_path.write_text("\n".join(lineas), encoding="utf-8")
    return output_path


def _lineas_reporte_integridad(hallazgos: List[Dict[str, Any]]) -> List[str]:
    lineas = [
        "REPORTE DE INTEGRIDAD DE DATOS",
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Objetivo: detectar datos en BD DESTINO que rompen o debilitan reglas esperadas por BD ORIGEN.",
        "",
        f"Total de hallazgos: {len(hallazgos)}",
        "",
    ]

    if not hallazgos:
        lineas.append("No se encontraron problemas de integridad de datos.")
    else:
        resumen_impacto: Dict[str, int] = {}
        resumen_tipo: Dict[str, int] = {}
        for hallazgo in hallazgos:
            resumen_impacto[hallazgo["impact"]] = resumen_impacto.get(hallazgo["impact"], 0) + 1
            resumen_tipo[hallazgo["type"]] = resumen_tipo.get(hallazgo["type"], 0) + 1

        lineas.append("Resumen por impacto:")
        for impacto in _impact_order():
            lineas.append(f"  {impacto}: {resumen_impacto.get(impacto, 0)}")
        lineas.append("")
        lineas.append("Resumen por tipo:")
        for tipo, total in sorted(resumen_tipo.items()):
            lineas.append(f"  {tipo}: {total}")
        lineas.append("")
        lineas.append("Detalle:")
        lineas.append("")

        index = 1
        for impacto in _impact_order():
            hallazgos_por_impacto = [
                hallazgo for hallazgo in hallazgos if hallazgo["impact"] == impacto
            ]
            if not hallazgos_por_impacto:
                continue

            lineas.append(f"IMPACTO {impacto}")
            lineas.append("")
            for hallazgo in hallazgos_por_impacto:
                lineas.extend(
                    [
                        f"{index}. {hallazgo['type']}",
                        f"   {hallazgo['description']}",
                        f"   Filas afectadas: {hallazgo['count']}",
                        f"   Motivo: {hallazgo['impact_reason']}",
                        "   Consulta de revision:",
                        f"   {hallazgo['review_sql']}",
                    ]
                )
                if hallazgo.get("fix_sql"):
                    lineas.extend(
                        [
                            "   SQL de fix propuesto:",
                            f"   {hallazgo['fix_sql']}",
                        ]
                    )
                lineas.append("")
                index += 1

    return lineas


def escribir_reporte_integridad(hallazgos: List[Dict[str, Any]]) -> Tuple[Path, Path]:
    """Genera reportes TXT y PDF con problemas de integridad de datos en DESTINO."""
    txt_path = Path("output/reporte_integridad_datos.txt")
    pdf_path = Path("output/reporte_integridad_datos.pdf")
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    lineas = _lineas_reporte_integridad(hallazgos)
    txt_path.write_text("\n".join(lineas), encoding="utf-8")
    SimplePDFReport(str(pdf_path)).write(lineas)
    return txt_path, pdf_path


def escribir_sql_fix_huerfanos(hallazgos: List[Dict[str, Any]]) -> Path:
    """Genera SQL de apoyo para corregir filas huerfanas detectadas."""
    output_path = Path("output/fix_huerfanos.sql")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    huerfanos = [
        hallazgo
        for hallazgo in hallazgos
        if hallazgo["type"] == "FK_ORPHAN_ROWS"
    ]

    lineas = [
        "-- SQL de fix propuesto para filas huerfanas.",
        "-- Revisa y prueba antes de ejecutar en un ambiente real.",
        "-- Los UPDATE automaticos solo se generan cuando la FK permite NULL.",
        "",
    ]

    if not huerfanos:
        lineas.append("-- No se encontraron filas huerfanas con fix generado.")
    else:
        for index, hallazgo in enumerate(huerfanos, start=1):
            lineas.extend(
                [
                    f"-- {index}. {hallazgo['type']}",
                    f"-- Impacto: {hallazgo['impact']}",
                    f"-- Filas afectadas: {hallazgo['count']}",
                    f"-- {hallazgo['description']}",
                    hallazgo.get("fix_sql") or "-- Sin fix automatico.",
                    "",
                ]
            )

    output_path.write_text("\n".join(lineas), encoding="utf-8")
    return output_path


def _impact_order() -> List[str]:
    return ["CRITICA", "ALTO", "MEDIO", "BAJO"]


def escribir_scripts(diferencias: List[Dict[str, Any]]) -> Path:
    """Genera el archivo SQL con las diferencias detectadas."""
    generator = SQLGenerator()
    generator.write(diferencias)
    return generator.output_path


def escribir_scripts_por_impacto(diferencias: List[Dict[str, Any]]) -> List[Path]:
    """Genera archivos SQL separados por nivel de impacto."""
    paths = []
    for impacto in _impact_order():
        diferencias_por_impacto = [
            diferencia for diferencia in diferencias if diferencia["impact"] == impacto
        ]
        output_name = f"output/scripts_{impacto.lower()}.sql"
        generator = SQLGenerator(output_name)
        generator.write(diferencias_por_impacto)
        paths.append(generator.output_path)

    return paths


def main() -> None:
    origen = DatabaseConnection(DB_ORIGEN_CONFIG)
    destino = DatabaseConnection(DB_DESTINO_CONFIG)

    print("Iniciando validacion de conexiones...")
    resultados = [
        validar_conexion("BD ORIGEN", origen),
        validar_conexion("BD DESTINO", destino),
    ]
    reporte_path = escribir_reporte_conexiones(resultados)

    print(f"Reporte generado: {reporte_path}")
    if any(resultado["estado"] == "ERROR" for resultado in resultados):
        print("No se compara metadata porque una o mas conexiones fallaron.")
        print("Proceso terminado.")
        return

    print("Comparando metadata...")
    diferencias = comparar_metadata(
        DatabaseConnection(DB_ORIGEN_CONFIG),
        DatabaseConnection(DB_DESTINO_CONFIG),
    )
    metadata_path = escribir_reporte_metadata(diferencias)
    scripts_path = escribir_scripts(diferencias)
    scripts_impacto_paths = escribir_scripts_por_impacto(diferencias)

    print("Revisando integridad de datos en BD DESTINO...")
    hallazgos_integridad = revisar_integridad_datos(
        DatabaseConnection(DB_ORIGEN_CONFIG),
        DatabaseConnection(DB_DESTINO_CONFIG),
    )
    integridad_txt_path, integridad_pdf_path = escribir_reporte_integridad(hallazgos_integridad)
    fix_huerfanos_path = escribir_sql_fix_huerfanos(hallazgos_integridad)

    print(f"Reporte de metadata generado: {metadata_path}")
    print(f"Reporte de integridad de datos TXT generado: {integridad_txt_path}")
    print(f"Reporte de integridad de datos PDF generado: {integridad_pdf_path}")
    print(f"SQL de fix para huerfanos generado: {fix_huerfanos_path}")
    print(f"Archivo SQL generado: {scripts_path}")
    for path in scripts_impacto_paths:
        print(f"Archivo SQL por impacto generado: {path}")
    print(f"Diferencias encontradas: {len(diferencias)}")
    print(f"Hallazgos de integridad encontrados: {len(hallazgos_integridad)}")
    print("Proceso terminado.")


if __name__ == "__main__":
    main()
