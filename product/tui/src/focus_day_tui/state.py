"""What the TUI keeps between runs, in `~/.focus-day/` (or `FOCUS_DAY_STATE_DIR`).

- `preferences.json`: assignee, working window, block minutes and time zone
  from the last successful run, used as the form's defaults.
- `last-focus-day.json`: the request, `plan` and `created` of the last run
  that failed partway, so Retry can send them back as `replay`, even after
  the app was closed. Deleted after any successful run.

These are conveniences for one person on one machine. A missing or unreadable
file is treated as empty; the API holds no state that depends on them.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATE_DIR_ENV = "FOCUS_DAY_STATE_DIR"
PREFERENCES_FILE = "preferences.json"
LAST_RUN_FILE = "last-focus-day.json"


def default_state_dir(environ: Mapping[str, str] = os.environ) -> Path:
    value = environ.get(STATE_DIR_ENV, "")
    return Path(value).expanduser() if value else Path.home() / ".focus-day"


@dataclass(frozen=True)
class Preferences:
    assignee: str = ""
    window_start: str = ""
    window_end: str = ""
    block_minutes: str = ""
    timezone: str = ""

    @classmethod
    def from_request(cls, body: Mapping[str, Any]) -> Preferences:
        """The remembered fields of a request the API accepted."""
        window = body.get("working_window") or {}
        minutes = body.get("default_minutes")
        return cls(
            assignee=str(body.get("assignee", "")),
            window_start=str(window.get("start", "")),
            window_end=str(window.get("end", "")),
            block_minutes="" if minutes is None else str(minutes),
            timezone=str(body.get("timezone") or ""),
        )


@dataclass(frozen=True)
class LastRun:
    """A run that can be finished with `replay`."""

    request: dict[str, Any]
    plan: dict[str, Any]
    created: dict[str, str]

    def replay_request(self) -> dict[str, Any]:
        """The original request with `replay` built from this run."""
        body = {key: value for key, value in self.request.items() if key != "replay"}
        body["replay"] = {"plan": self.plan, "created": self.created}
        return body


class LocalStateStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def last_run_path(self) -> Path:
        return self.directory / LAST_RUN_FILE

    def load_preferences(self) -> Preferences:
        data = self._read(PREFERENCES_FILE)
        if data is None:
            return Preferences()
        return Preferences(
            **{
                key: value
                for key, value in data.items()
                if key in Preferences.__dataclass_fields__ and isinstance(value, str)
            }
        )

    def save_preferences(self, preferences: Preferences) -> None:
        self._write(PREFERENCES_FILE, asdict(preferences))

    def load_last_run(self) -> LastRun | None:
        data = self._read(LAST_RUN_FILE)
        if data is None:
            return None
        request, plan, created = (
            data.get("request"),
            data.get("plan"),
            data.get("created"),
        )
        if not (
            isinstance(request, dict)
            and isinstance(plan, dict)
            and isinstance(created, dict)
            and all(
                isinstance(k, str) and isinstance(v, str) for k, v in created.items()
            )
        ):
            return None
        return LastRun(request=request, plan=plan, created=created)

    def save_last_run(self, run: LastRun) -> None:
        self._write(LAST_RUN_FILE, asdict(run))

    def clear_last_run(self) -> None:
        self.last_run_path.unlink(missing_ok=True)

    def _read(self, name: str) -> dict[str, Any] | None:
        try:
            data = json.loads((self.directory / name).read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _write(self, name: str, data: dict[str, Any]) -> None:
        """Write atomically, so a crash never leaves half a file. Raises OSError."""
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
