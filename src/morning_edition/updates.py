"""One-click updates: ask the Watchtower updater container whether a newer image is published, and to
pull it and restart this app. Every failure comes back as a result, never as an exception, and nothing
here ever puts the updater token (or text that might carry it) in a message, a log line, or a file."""
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ValidationError

from . import scheduler
from .config import AppConfig
from .settings import Settings

log = logging.getLogger("morning_edition.updates")

STATE_FILE = "updates.json"
CHECK_TIMEOUT = httpx.Timeout(10.0, read=150.0)    # the updater itself waits up to 2 minutes for the registry
UPDATE_TIMEOUT = httpx.Timeout(10.0, read=120.0)
GITHUB_TIMEOUT = httpx.Timeout(10.0)
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_state_lock = threading.Lock()


class CheckResult(BaseModel):
    update_available: bool = False
    current_version: str = ""
    error: str = ""


class UpdateRequestResult(BaseModel):
    started: bool = False
    error: str = ""


class UpdateStatus(BaseModel):
    """What the Updates section of Settings shows; kept in <data_dir>/updates.json."""
    checked_at: str = ""
    check_result: Literal["", "up_to_date", "update_available", "error"] = ""
    check_error: str = ""
    latest_sha: str = ""
    latest_message: str = ""
    latest_date: str = ""
    update_requested_at: str = ""
    update_result: Literal["", "requested", "started", "failed"] = ""
    update_error: str = ""
    auto_at: str = ""
    auto_note: str = ""


# ---- state file ------------------------------------------------------------------------------

def load_status(data_dir: Path) -> UpdateStatus:
    try:
        return UpdateStatus.model_validate_json((data_dir / STATE_FILE).read_text())
    except (OSError, ValueError, ValidationError):
        return UpdateStatus()


def record(data_dir: Path, **fields) -> UpdateStatus:
    """Merge fields into the state file (written atomically) and return the new status."""
    with _state_lock:
        status = load_status(data_dir).model_copy(update=fields)
        path = data_dir / STATE_FILE
        tmp = path.with_name(STATE_FILE + ".tmp")
        try:
            tmp.write_text(status.model_dump_json(indent=2))
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("Could not save update status: %s", type(exc).__name__)
        return status


def format_when(iso: str, tz_name: str) -> str:
    """A stored UTC timestamp as "Oct 12, 2:00 AM" in the paper's timezone, or "" if unreadable."""
    try:
        moment = datetime.fromisoformat(iso).astimezone(ZoneInfo(tz_name))
    except (ValueError, KeyError, OSError):
        return ""
    return moment.strftime("%b %-d, %-I:%M %p")


def configured(cfg: AppConfig) -> bool:
    return bool(cfg.watchtower_url and cfg.watchtower_token)


def commits_url(repo: str) -> str:
    return f"https://github.com/{repo}/commits/main" if _REPO_RE.fullmatch(repo) else ""


# ---- the updater's API -----------------------------------------------------------------------

def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _failure(action: str, exc: Exception) -> str:
    """A log-safe description: the exception's class only, since its text can hold URLs or headers."""
    log.warning("Updater %s failed: %s", action, type(exc).__name__)
    return "The updater could not be reached. Check that the watchtower container is running."


def check(client: httpx.Client, url: str, token: str, image: str) -> CheckResult:
    """Ask the updater whether a newer image is published (registry digest only, no download)."""
    try:
        r = client.post(f"{url.rstrip('/')}/v1/check", params={"image": image, "timeout": "2m"},
                        headers=_headers(token), timeout=CHECK_TIMEOUT)
    except httpx.HTTPError as exc:
        return CheckResult(error=_failure("check", exc))
    if r.status_code in (401, 403):
        return CheckResult(error="The updater refused the token. Make sure WATCHTOWER_TOKEN matches in .env.")
    if r.status_code != 200:
        log.warning("Updater check answered HTTP %d", r.status_code)
        return CheckResult(error=f"The updater answered with an error (HTTP {r.status_code}).")
    try:
        containers = r.json()["containers"]
        if not isinstance(containers, list) or not all(isinstance(c, dict) for c in containers):
            raise ValueError("containers")
    except (ValueError, KeyError, TypeError):
        return CheckResult(error="The updater's answer was not understood.")
    if not containers:
        return CheckResult(error="The updater does not see this app. Check that the container has the "
                                 "com.centurylinklabs.watchtower.enable=true label.")
    problem = next((str(c["error"]) for c in containers if c.get("error")), "").replace(token, "[token]")
    if problem:
        return CheckResult(error=f"The updater could not check for a new image: {problem[:200]}")
    return CheckResult(update_available=any(c.get("update_available") is True for c in containers),
                       current_version=str(containers[0].get("current_image_version") or ""))


def request_update(client: httpx.Client, url: str, token: str, image: str, data_dir: Path,
                   now: datetime | None = None) -> UpdateRequestResult:
    """Tell the updater to pull and restart this app. The app is replaced mid-request, so a connection
    dropped or timed out after sending counts as "update started". The request is saved first."""
    record(data_dir, update_requested_at=(now or datetime.now(UTC)).isoformat(), update_result="requested",
           update_error="")
    try:
        r = client.post(f"{url.rstrip('/')}/v1/update", params={"image": image},
                        headers=_headers(token), timeout=UPDATE_TIMEOUT)
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout):
        record(data_dir, update_result="started")
        return UpdateRequestResult(started=True)
    except httpx.HTTPError as exc:
        error = _failure("update", exc)
        record(data_dir, update_result="failed", update_error=error)
        return UpdateRequestResult(error=error)
    if r.status_code in (401, 403):
        error = "The updater refused the token. Make sure WATCHTOWER_TOKEN matches in .env."
    elif r.status_code >= 400:
        log.warning("Updater update answered HTTP %d", r.status_code)
        error = f"The updater answered with an error (HTTP {r.status_code})."
    else:
        record(data_dir, update_result="started")
        return UpdateRequestResult(started=True)
    record(data_dir, update_result="failed", update_error=error)
    return UpdateRequestResult(error=error)


# ---- GitHub ----------------------------------------------------------------------------------

def latest_release_info(client: httpx.Client, repo: str) -> tuple[str, str, str] | None:
    """The newest commit on main as (short sha, first line of its message, YYYY-MM-DD), or None."""
    if not _REPO_RE.fullmatch(repo):
        return None
    try:
        r = client.get(f"https://api.github.com/repos/{repo}/commits/main", timeout=GITHUB_TIMEOUT,
                       headers={"Accept": "application/vnd.github+json", "User-Agent": "morning-edition"})
        if r.status_code != 200:
            return None
        data = r.json()
        sha = data["sha"]
        message = data["commit"]["message"]
        date = data["commit"]["committer"]["date"]
        if not all(isinstance(x, str) for x in (sha, message, date)):
            return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    return sha[:7], message.strip().split("\n", 1)[0].strip(), date[:10]


def run_check(client: httpx.Client, cfg: AppConfig, now: datetime | None = None) -> UpdateStatus:
    """Check the updater and GitHub, save the outcome, and return it."""
    result = check(client, cfg.watchtower_url, cfg.watchtower_token, cfg.app_image)
    fields: dict = {"checked_at": (now or datetime.now(UTC)).isoformat(),
                    "check_error": result.error,
                    "check_result": "error" if result.error else
                                    ("update_available" if result.update_available else "up_to_date")}
    info = latest_release_info(client, cfg.app_repo)
    if info:
        fields.update(latest_sha=info[0], latest_message=info[1], latest_date=info[2])
    return record(cfg.data_dir, **fields)


def nightly_update(client: httpx.Client, cfg: AppConfig, s: Settings, build_running: bool,
                   now: datetime | None = None) -> str:
    """The overnight job: check, and request an update only if one is available. Skips (and records
    why) when no updater is set up, an edition is building, or the next edition builds within the hour."""
    now = now or datetime.now(UTC)

    def finish(note: str) -> str:
        record(cfg.data_dir, auto_at=now.isoformat(), auto_note=note)
        log.info("Overnight update: %s", note)
        return note

    if not configured(cfg):
        return finish("Skipped, the updater service is not set up.")
    if build_running:
        return finish("Skipped, an edition is building.")
    if scheduler.build_is_imminent(s, now):
        return finish("Skipped, the next edition builds within the hour.")
    status = run_check(client, cfg, now)
    if status.check_result == "error":
        return finish(f"The check failed, so no update was requested. {status.check_error}")
    if status.check_result != "update_available":
        return finish("Up to date.")
    # A successful update kills this process mid-request, so the success note is saved first.
    finish("Update requested.")
    result = request_update(client, cfg.watchtower_url, cfg.watchtower_token, cfg.app_image, cfg.data_dir, now)
    return "Update requested." if result.started else finish(f"The update request failed. {result.error}")
