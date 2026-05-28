from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib as toml
except ModuleNotFoundError:  # Python < 3.11
    import tomli as toml

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "paprika-mcp"
CONFIG_FILE = CONFIG_DIR / "config.toml"
TOKEN_CACHE_FILE = CONFIG_DIR / ".paprika_token.json"


@dataclass
class Settings:
    paprika_email: str
    paprika_password: str
    paprika_port: int = 8000
    paprika_host: str = "127.0.0.1"
    paprika_db_path: Path = CONFIG_DIR / "paprika.sqlite"


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config_file() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    data = toml.loads(CONFIG_FILE.read_text())
    return data if isinstance(data, dict) else {}


def save_config(email: str, password: str, port: int) -> None:
    ensure_config_dir()
    lines = ["[paprika]", f"email = \"{email}\"", f"password = \"{password}\"", f"port = {port}"]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")
    CONFIG_FILE.chmod(0o600)


def get_settings(overrides: Optional[Dict[str, str]] = None) -> Settings:
    data = load_config_file()
    paprika = data.get("paprika", {}) if isinstance(data.get("paprika"), dict) else {}

    email = paprika.get("email")
    password = paprika.get("password")
    port = paprika.get("port", 8000)
    host = paprika.get("host", "127.0.0.1")
    db_path = paprika.get("db_path")

    import os

    email = os.getenv("PAPRIKA_EMAIL", email)
    password = os.getenv("PAPRIKA_PASSWORD", password)
    env_port = os.getenv("PAPRIKA_PORT")
    if env_port:
        port = env_port
    host = os.getenv("PAPRIKA_HOST", host)
    db_path = os.getenv("PAPRIKA_DB_PATH", db_path)

    if overrides:
        email = overrides.get("email", email)
        password = overrides.get("password", password)
        if "port" in overrides and overrides["port"] is not None:
            port = overrides["port"]
        host = overrides.get("host", host)
        db_path = overrides.get("db_path", db_path)

    if not email or not password:
        raise ValueError("Missing Paprika credentials. Run 'paprika-mcp setup'.")

    try:
        port = int(port)
    except Exception as exc:
        raise ValueError(f"Invalid port: {port}") from exc

    resolved_db_path = Path(db_path).expanduser() if db_path else CONFIG_DIR / "paprika.sqlite"
    if not resolved_db_path.is_absolute():
        resolved_db_path = CONFIG_DIR / resolved_db_path

    return Settings(
        paprika_email=email,
        paprika_password=password,
        paprika_port=port,
        paprika_host=str(host),
        paprika_db_path=resolved_db_path,
    )


def token_cache_path() -> Path:
    ensure_config_dir()
    return TOKEN_CACHE_FILE
