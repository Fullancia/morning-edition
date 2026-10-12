import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IMAGE = "ghcr.io/rickgram/morning-edition"
DEFAULT_REPO = "rickgram/morning-edition"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    dashboard_password: str
    session_secret: str
    # One-click updates: the Watchtower updater's address and token, and what this build is.
    watchtower_url: str = ""
    watchtower_token: str = field(default="", repr=False)   # a secret: never logged or rendered
    app_version: str = ""
    app_commit: str = ""
    app_image: str = DEFAULT_IMAGE
    app_repo: str = DEFAULT_REPO


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
        watchtower_url=env.get("WATCHTOWER_URL", "").strip().rstrip("/"),
        watchtower_token=env.get("WATCHTOWER_TOKEN", "").strip(),
        app_version=env.get("APP_VERSION", "").strip(),
        app_commit=env.get("APP_COMMIT", "").strip(),
        app_image=env.get("APP_IMAGE", "").strip() or DEFAULT_IMAGE,
        app_repo=env.get("APP_REPO", "").strip() or DEFAULT_REPO,
    )
