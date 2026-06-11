import os
from typing import Dict

from dotenv import load_dotenv


load_dotenv()


DEFAULT_DB_URL = "jdbc:postgresql://172.17.1.163:5432/siox_qa"


def _get_connection_config(prefix: str) -> Dict[str, str]:
    """Lee configuracion global o especifica por BD desde variables de entorno."""
    return {
        "url": (
            os.getenv(f"{prefix}_HOST")
            or os.getenv(f"{prefix}_URL")
            or os.getenv("DB_HOST")
            or DEFAULT_DB_URL
        ),
        "user": os.getenv(f"{prefix}_USER") or os.getenv("DB_USER") or "",
        "password": os.getenv(f"{prefix}_PASSWORD") or os.getenv("DB_PASSWORD") or "",
        "schema": os.getenv(f"{prefix}_SCHEMA") or os.getenv("DB_SCHEMA") or "",
        "engine": os.getenv(f"{prefix}_ENGINE") or os.getenv("DB_ENGINE") or "",
    }


DB_ORIGEN_CONFIG = _get_connection_config("DB_ORIGEN")
DB_DESTINO_CONFIG = _get_connection_config("DB_DESTINO")
