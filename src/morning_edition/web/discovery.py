"""The feed finder's saved state: one small JSON file in the data directory."""
import json
import os
from pathlib import Path

from pydantic import ValidationError

from ..sources.discover import DiscoveredFeed
from .paper import is_web_url

STATE_FILE = "discovery.json"
INTERRUPTED = "The last search was interrupted. Search again."


def load_state(data_dir: Path, searching: bool) -> dict | None:
    """The saved state with `results` parsed into DiscoveredFeed. A search marked running that
    nothing is working on any more (the app restarted) shows as an error."""
    try:
        raw = json.loads((data_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("status") not in ("running", "done", "error"):
        return None
    results = []
    for item in raw.get("results") or []:
        try:
            results.append(DiscoveredFeed.model_validate(item))
        except ValidationError:
            continue
    state = {"status": raw["status"], "started_at": str(raw.get("started_at") or ""),
             "finished_at": raw.get("finished_at"), "error": raw.get("error"), "results": results}
    if state["status"] == "running" and not searching:
        state.update(status="error", error=INTERRUPTED)
    return state


def save_state(data_dir: Path, status: str, started_at: str, finished_at: str | None = None,
               error: str | None = None, results: list[DiscoveredFeed] | None = None) -> None:
    payload = {"status": status, "started_at": started_at, "finished_at": finished_at, "error": error,
               "results": [r.model_dump() for r in results or []]}
    tmp = data_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, data_dir / STATE_FILE)  # a reader never sees half a file


def group_by_section(results: list[DiscoveredFeed], order: list[str]) -> list[dict]:
    """Rows for the review form, grouped in section order. Each row keeps its index in `results`,
    which is what the add form posts back."""
    groups: dict[str, list[dict]] = {name: [] for name in order}
    for i, r in enumerate(results):
        groups.setdefault(r.section, []).append(
            {"i": i, "r": r, "homepage_link": r.homepage if is_web_url(r.homepage) else None})
    return [{"name": name, "rows": rows} for name, rows in groups.items() if rows]
