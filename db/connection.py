from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


class DatabaseConnection:
    """Administra conexiones JDBC-like para PostgreSQL, MySQL y SQL Server."""

    SUPPORTED_ENGINES = {"postgresql", "mysql", "sqlserver"}

    def __init__(self, config: Dict[str, str]) -> None:
        self.config = config
        self.connection: Optional[Any] = None
        self.parsed_url = self._parse_jdbc_url(config["url"])
        self._apply_engine_override()

    def connect(self) -> Any:
        """Crea la conexion si aun no existe y la devuelve."""
        if self.connection is not None:
            return self.connection

        engine = self.parsed_url["engine"]

        try:
            if engine == "postgresql":
                self.connection = self._connect_postgresql()
            elif engine == "mysql":
                self.connection = self._connect_mysql()
            elif engine == "sqlserver":
                self.connection = self._connect_sqlserver()
            else:
                raise ValueError(f"Motor no soportado: {engine}")

            return self.connection
        except Exception as error:
            raise ConnectionError(
                f"Error conectando a '{self.parsed_url['database']}' "
                f"en {self.parsed_url['host']}:{self.parsed_url['port']} "
                f"usando motor '{engine}'. Detalle tecnico: {error}"
            ) from error

    def test_connection(self) -> Dict[str, Any]:
        """Valida la conexion usando una consulta minima por motor."""
        connection = self.connect()
        engine = self.parsed_url["engine"]

        queries = {
            "postgresql": "SELECT current_database() AS database, current_schema() AS schema;",
            "mysql": "SELECT DATABASE() AS database, DATABASE() AS schema;",
            "sqlserver": "SELECT DB_NAME() AS database, SCHEMA_NAME() AS schema;",
        }

        cursor = connection.cursor()
        try:
            cursor.execute(queries[engine])
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("La base de datos no devolvio informacion de conexion.")

            return self._normalize_result(cursor, row)
        finally:
            cursor.close()

    def close(self) -> None:
        """Cierra la conexion si esta abierta."""
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connect_postgresql(self) -> Any:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        connection_params = {
            "host": self.parsed_url["host"],
            "port": self.parsed_url["port"],
            "database": self.parsed_url["database"],
            "user": self.config["user"],
            "password": self.config["password"],
            "cursor_factory": RealDictCursor,
        }

        options = self._postgres_search_path_options()
        if options:
            connection_params["options"] = options

        return psycopg2.connect(**connection_params)

    def _connect_mysql(self) -> Any:
        import mysql.connector

        return mysql.connector.connect(
            host=self.parsed_url["host"],
            port=self.parsed_url["port"],
            database=self.parsed_url["database"],
            user=self.config["user"],
            password=self.config["password"],
        )

    def _connect_sqlserver(self) -> Any:
        import pyodbc

        driver = self.parsed_url["params"].get("driver", ["ODBC Driver 18 for SQL Server"])[0]
        encrypt = self.parsed_url["params"].get("encrypt", ["yes"])[0]
        trust_cert = self.parsed_url["params"].get("trustServerCertificate", ["yes"])[0]
        connection_string = (
            f"DRIVER={{{driver}}};"
            f"SERVER={self.parsed_url['host']},{self.parsed_url['port']};"
            f"DATABASE={self.parsed_url['database']};"
            f"UID={self.config['user']};"
            f"PWD={self.config['password']};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_cert};"
        )
        return pyodbc.connect(connection_string)

    def _parse_jdbc_url(self, jdbc_url: str) -> Dict[str, Any]:
        normalized_url = jdbc_url.strip()
        if normalized_url.startswith("jdbc:"):
            normalized_url = normalized_url[5:]

        if normalized_url.startswith("sqlserver://"):
            return self._parse_sqlserver_url(normalized_url)

        parsed = urlparse(normalized_url)
        engine = self._normalize_engine(parsed.scheme)

        if engine not in self.SUPPORTED_ENGINES:
            raise ValueError(
                "Motor no soportado. Usa jdbc:postgresql, jdbc:mysql o jdbc:sqlserver."
            )

        return {
            "engine": engine,
            "host": parsed.hostname or "",
            "port": parsed.port or self._default_port(engine),
            "database": self._database_name(engine, parsed),
            "params": parse_qs(parsed.query),
        }

    def _parse_sqlserver_url(self, sqlserver_url: str) -> Dict[str, Any]:
        raw_server = sqlserver_url.replace("sqlserver://", "", 1)
        server_part, _, options_part = raw_server.partition(";")
        host_part, _, port_part = server_part.partition(":")
        params = self._parse_sqlserver_options(options_part)

        return {
            "engine": "sqlserver",
            "host": host_part,
            "port": int(port_part) if port_part else self._default_port("sqlserver"),
            "database": (
                params.get("databaseName", [""])[0]
                or params.get("database", [""])[0]
            ),
            "params": params,
        }

    def _parse_sqlserver_options(self, options_part: str) -> Dict[str, List[str]]:
        params: Dict[str, List[str]] = {}
        for option in options_part.split(";"):
            if not option or "=" not in option:
                continue

            key, value = option.split("=", 1)
            params[key] = [value]

        return params

    def _normalize_engine(self, engine: str) -> str:
        aliases = {
            "postgres": "postgresql",
            "postgresql": "postgresql",
            "mysql": "mysql",
            "sqlserver": "sqlserver",
            "mssql": "sqlserver",
        }
        return aliases.get(engine.lower(), engine.lower())

    def _apply_engine_override(self) -> None:
        configured_engine = self.config.get("engine", "").strip()
        if not configured_engine:
            return

        engine = self._normalize_engine(configured_engine)
        if engine not in self.SUPPORTED_ENGINES:
            raise ValueError(
                "Motor no soportado en variable ENGINE. Usa postgresql, mysql o sqlserver."
            )

        self.parsed_url["engine"] = engine

    def _default_port(self, engine: str) -> int:
        return {
            "postgresql": 5432,
            "mysql": 3306,
            "sqlserver": 1433,
        }[engine]

    def _database_name(self, engine: str, parsed_url: Any) -> str:
        if engine == "sqlserver":
            params = parse_qs(parsed_url.query.replace(";", "&"))
            return (
                params.get("databaseName", [""])[0]
                or params.get("database", [""])[0]
                or parsed_url.path.strip("/")
            )

        return parsed_url.path.strip("/")

    def _normalize_result(self, cursor: Any, row: Any) -> Dict[str, Any]:
        if isinstance(row, dict):
            result = dict(row)
        else:
            columns = [column[0] for column in cursor.description]
            result = dict(zip(columns, row))

        if not result.get("schema") and self.config.get("schema"):
            result["schema"] = self.config["schema"]

        return result

    def _postgres_search_path_options(self) -> str:
        schema = self.config.get("schema", "").strip()
        if not schema:
            return ""

        escaped_schema = schema.replace('"', '""')
        return f'-c search_path="{escaped_schema}"'
