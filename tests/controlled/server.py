"""Entrypoint: the real FastAPI app with the in-memory provider injected.

    GIT_SHA="$(git rev-parse HEAD)" uv run --locked uvicorn tests.controlled.server:app

Then run the conformance suite against it:

    CONFORMANCE_BASE_URL=http://127.0.0.1:8000 uv run --locked pytest contract/tests/conformance

Startup still validates configuration, so placeholder Trello settings are
supplied when none are set. They are never used: every request goes to the
in-memory provider, and nothing contacts Trello. Set CONTROLLED_FAULT to a
name in `provider.FAULTS` to start a deliberately broken service.

Importing this module changes the shared `app.main.app`, so only uvicorn
imports it; tests use `provider.dependency_overrides` with monkeypatch.
"""

import os
import tempfile
from pathlib import Path

from app.main import app
from tests.controlled.provider import dependency_overrides

FAULT_VARIABLE = "CONTROLLED_FAULT"

# Startup builds (but never uses) the Trello adapter, whatever the shell says.
os.environ["ISSUE_PROVIDER"] = "trello"

# Placeholders that pass startup validation. Not credentials.
_PLACEHOLDER_CONFIG = {
    "TRELLO_API_KEY": "controlled-placeholder-key",
    "TRELLO_TOKEN": "controlled-placeholder-token",
    "TRELLO_LIST_ID": "000000000000000000000000",
    "IDEMPOTENCY_DB_PATH": str(
        Path(tempfile.gettempdir()) / "controlled-idempotency.sqlite3"
    ),
}

for _name, _value in _PLACEHOLDER_CONFIG.items():
    os.environ.setdefault(_name, _value)

app.dependency_overrides.update(
    dependency_overrides(os.environ.get(FAULT_VARIABLE) or None)
)

__all__ = ["app"]
