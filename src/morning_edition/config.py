import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    dashboard_password: str
    session_secret: str


def load_config(env: Mapping[str, str] = os.environ) -> AppConfig:
    password = env.get("DASHBOARD_PASSWORD", "")
    if not password:
        raise RuntimeError("DASHBOARD_PASSWORD must be set")
    secret = env.get("SESSION_SECRET") or hashlib.sha256(
        ("morning-edition:" + password).encode()
    ).hexdigest()
    return AppConfig(
        data_dir=Path(env.get("DATA_DIR", "./data")).resolve(),
        dashboard_password=password,
        session_secret=secret,
    )
