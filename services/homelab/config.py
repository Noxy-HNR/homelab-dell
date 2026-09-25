"""Shared, explicit server configuration."""
from dataclasses import dataclass
from pathlib import Path
import json
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    role: str
    state_dir: Path
    models_dir: Path
    projects_dir: Path
    config_dir: Path
    hp_url: str = ""
    dell_url: str = ""
    token: str = ""
    allow_network_downloads: bool = True


def load_settings(path: str | Path) -> Settings:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("role") not in ("hp", "dell"):
        raise ValueError("role must be hp or dell")
    token = (Path(data["token_file"]).read_text(encoding="utf-8").strip()
             if data.get("token_file") else data.get("token", ""))
    if len(token) < 32:
        raise ValueError("The login token must contain at least 32 characters")
    values = {key: Path(data[key]).resolve() for key in (
        "state_dir", "models_dir", "projects_dir", "config_dir")}
    for key in ("hp_url", "dell_url"):
        value = data.get(key, "")
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f"Invalid {key}")
    return Settings(role=data["role"], token=token, **values,
                    hp_url=data.get("hp_url", ""), dell_url=data.get("dell_url", ""),
                    allow_network_downloads=data.get("allow_network_downloads", True))
