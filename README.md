# Schema Comparator Tool

Herramienta en Python para comparar metadata de bases de datos y generar scripts SQL para alinear una BD DESTINO con una BD ORIGEN.

El flujo principal del proyecto:

1. Valida conexión a BD ORIGEN y BD DESTINO.
2. Lee metadata estructural.
3. Compara diferencias.
4. Revisa integridad de datos en BD DESTINO contra reglas esperadas por BD ORIGEN.
5. Genera reportes TXT.
6. Genera scripts SQL generales y por nivel de impacto.

## Características

- Validación de conexión para PostgreSQL, MySQL y SQL Server.
- Comparación de metadata estructural (actualmente implementada para PostgreSQL).
- Detección de diferencias en:
	- Esquema
	- Secuencias
	- Tablas
	- Columnas
	- Constraints
	- Índices
	- Vistas
- Revisión de integridad de datos en DESTINO:
	- Valores NULL en columnas obligatorias.
	- Relaciones FK con columnas en NULL.
	- Filas huérfanas que no tienen registro padre.
	- Duplicados que romperían PRIMARY KEY o UNIQUE.
	- Textos obligatorios vacíos.
- Clasificación de impacto por diferencia:
	- CRITICA
	- ALTO
	- MEDIO
	- BAJO
- Generación de archivos de salida listos para revisión.

## Requisitos

- Python 3.10 o superior (recomendado).
- Acceso de red a las bases de datos.
- Drivers según el motor que uses:
	- PostgreSQL: psycopg2-binary
	- MySQL: mysql-connector-python
	- SQL Server: pyodbc (y driver ODBC instalado en el sistema)

Dependencias Python (ya listadas en requirements.txt):

- psycopg2-binary>=2.9
- python-dotenv>=1.0
- mysql-connector-python>=8.0
- pyodbc>=5.0

## Instalación

1. Crear y activar entorno virtual (opcional, recomendado):

	 Windows PowerShell:

	 ```powershell
	 python -m venv .venv
	 .\.venv\Scripts\Activate.ps1
	 ```

2. Instalar dependencias:

	 ```powershell
	 pip install -r requirements.txt
	 ```

## Configuración

La aplicación carga variables de entorno desde un archivo .env (si existe) y también desde el entorno del sistema.

### Variables soportadas

Configuración específica por conexión:

- DB_ORIGEN_HOST o DB_ORIGEN_URL
- DB_ORIGEN_USER
- DB_ORIGEN_PASSWORD
- DB_ORIGEN_SCHEMA
- DB_ORIGEN_ENGINE

- DB_DESTINO_HOST o DB_DESTINO_URL
- DB_DESTINO_USER
- DB_DESTINO_PASSWORD
- DB_DESTINO_SCHEMA
- DB_DESTINO_ENGINE

Variables globales (fallback):

- DB_HOST
- DB_USER
- DB_PASSWORD
- DB_SCHEMA
- DB_ENGINE

Notas importantes:

- Si no defines URL/HOST, se usa un valor por defecto interno.
- El campo ENGINE es opcional; si no se indica, se intenta inferir desde la URL.
- Motores soportados: postgresql, mysql, sqlserver.

### Ejemplo de archivo .env

```env
# ORIGEN
DB_ORIGEN_URL=jdbc:postgresql://localhost:5432/mi_bd_origen
DB_ORIGEN_USER=postgres
DB_ORIGEN_PASSWORD=postgres
DB_ORIGEN_SCHEMA=public
DB_ORIGEN_ENGINE=postgresql

# DESTINO
DB_DESTINO_URL=jdbc:postgresql://localhost:5432/mi_bd_destino
DB_DESTINO_USER=postgres
DB_DESTINO_PASSWORD=postgres
DB_DESTINO_SCHEMA=public
DB_DESTINO_ENGINE=postgresql
```

También puedes usar formato SQL Server JDBC-like, por ejemplo:

```text
jdbc:sqlserver://localhost:1433;databaseName=MiDB;encrypt=yes;trustServerCertificate=yes
```

## Ejecución

Desde la raíz del proyecto:

```powershell
python main.py
```

### Dashboard grafico

Tambien puedes abrir una interfaz grafica con Tkinter:

```powershell
python gui.py
```

El dashboard permite:

- Validar la conexion de BD ORIGEN y BD DESTINO.
- Comparar si la estructura de ambas bases es igual.
- Revisar inconsistencias de integridad de datos en BD DESTINO.
- Ejecutar el flujo completo desde un solo boton.
- Ver el avance del proceso en un cuadro de texto.
- Cambiar entre modo claro y modo oscuro.
- Ver una barra de progreso mientras se ejecuta cada proceso.
- Descargar de forma independiente el PDF de conexiones, estructura o integridad.
- Descargar los scripts SQL generados.
- Vaciar los datos de una tabla en BD ORIGEN o BD DESTINO desde un dialogo seguro.
- Analizar previamente todas las tablas dependientes por claves foraneas.
- Confirmar el borrado despues de revisar las filas y el orden de ejecucion.
- Generar un reporte final TXT y PDF con las filas borradas.

## Archivos de salida

Se generan en la carpeta output:

- reporte_conexiones.txt
	- Estado de conectividad por cada BD.
- reporte_metadata.txt
	- Resumen y detalle de diferencias detectadas.
- reporte_integridad_datos.txt
	- Resumen de problemas de datos que afectan integridad y consultas SQL de revisión.
- reporte_integridad_datos.pdf
	- Mismo reporte de integridad en formato PDF.
- reporte_borrado_datos.txt
- reporte_borrado_datos.pdf
	- Resultado del ultimo vaciado de tabla ejecutado desde el dashboard.
- fix_huerfanos.sql
	- SQL de apoyo para corregir filas huérfanas detectadas; solo genera UPDATE automático cuando la FK permite NULL.
- scripts.sql
	- Script SQL consolidado.
- scripts_critica.sql
- scripts_alto.sql
- scripts_medio.sql
- scripts_bajo.sql
	- Scripts SQL filtrados por impacto.

## Tipos de diferencias detectadas

Ejemplos de type en resultados:

- SCHEMA_MISSING
- SEQUENCE_MISSING
- TABLE_MISSING
- COLUMN_MISSING
- COLUMN_DIFFERENT
- CONSTRAINT_MISSING_OR_DIFFERENT
- INDEX_MISSING_OR_DIFFERENT
- VIEW_MISSING_OR_DIFFERENT

Ejemplos de type en hallazgos de integridad:

- REQUIRED_NULL_VALUES
- FK_NULL_RELATION
- FK_ORPHAN_ROWS
- DUPLICATE_KEY_VALUES
- REQUIRED_EMPTY_TEXT

Cada diferencia incluye:

- type
- impact
- impact_reason
- description
- sql

Cada hallazgo de integridad incluye:

- type
- impact
- impact_reason
- description
- count
- review_sql

## Estructura del proyecto

```text
schema_comparator_tool/
	config.py
	main.py
	requirements.txt
	comparator/
		schema_comparator.py
	db/
		connection.py
		metadata.py
	generator/
		sql_generator.py
	output/
		reporte_conexiones.txt
		reporte_metadata.txt
		scripts.sql
		scripts_critica.sql
		scripts_alto.sql
		scripts_medio.sql
		scripts_bajo.sql
```

## Limitaciones actuales

- La comparación completa de metadata está implementada actualmente para PostgreSQL.
- Aunque existen conectores para MySQL y SQL Server en validación de conexión, la lectura detallada de metadata para esos motores aún no está implementada.
- El vaciado de tablas soporta PostgreSQL, MySQL y SQL Server. Si detecta un ciclo de claves foraneas que impide determinar un orden seguro, no ejecuta ningun borrado.

## Buenas prácticas recomendadas

- Revisar manualmente scripts.sql antes de ejecutar.
- Ejecutar primero scripts_critica.sql en ambiente de pruebas y validar.
- Tomar backup de la BD destino antes de aplicar cambios.
- Aplicar cambios de mayor a menor impacto y verificar resultados.

## Solución de problemas

- Error de conexión:
	- Verifica URL, credenciales, red y puerto.
- Motor no soportado:
	- Usa uno de: postgresql, mysql, sqlserver.
- Error con pyodbc en SQL Server:
	- Instala un driver ODBC compatible y verifica el nombre del driver en la URL/parámetros.

## Próximas mejoras sugeridas

- Soporte de comparación de metadata para MySQL y SQL Server.
- Exclusión configurable de tablas/objetos.
- Modo dry-run detallado con diff más granular.
- Exportación adicional en JSON/CSV.
