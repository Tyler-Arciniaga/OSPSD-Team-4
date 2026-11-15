"""The Trello package must not depend on the service, a framework or telemetry.

Consumers install it into their own services, so it may use only the standard
library, the shared contract (and pydantic, which the contract brings), and
its own modules. Importing `app`, FastAPI or OpenTelemetry would collide with
or bloat the consumer's service.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

PACKAGE_NAME = "team_4_issue_tracker_trello"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / PACKAGE_NAME
ALLOWED_THIRD_PARTY = frozenset({"issue_tracker_contract", "pydantic"})


def forbidden_imports(source: str) -> list[str]:
    """Return the top-level module names `source` imports that are not allowed."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative imports stay inside the package.
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            allowed = (
                top == PACKAGE_NAME
                or top in sys.stdlib_module_names
                or top in ALLOWED_THIRD_PARTY
            )
            if not allowed:
                found.append(name)
    return found


def test_package_sources_exist() -> None:
    # Guards against the boundary check passing because it scanned nothing.
    names = {path.name for path in PACKAGE_DIR.glob("*.py")}
    assert {"__init__.py", "_common.py", "_tracing.py", "provider.py"} <= names


def test_package_imports_nothing_from_service_framework_or_telemetry() -> None:
    violations = {
        str(path.relative_to(PACKAGE_DIR)): bad
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if (bad := forbidden_imports(path.read_text(encoding="utf-8")))
    }
    assert violations == {}, (
        "team_4_issue_tracker_trello must not import service, framework, or "
        f"telemetry code: {violations}"
    )


def test_boundary_check_rejects_service_framework_and_telemetry_imports() -> None:
    source = """
import json
from issue_tracker_contract.models import Issue
from team_4_issue_tracker_trello import _common
from . import provider
from app.telemetry import provider_span
import app
from fastapi import FastAPI
from opentelemetry.trace import Span
"""
    assert forbidden_imports(source) == [
        "app.telemetry",
        "app",
        "fastapi",
        "opentelemetry.trace",
    ]


def test_importing_the_package_loads_no_service_framework_or_telemetry() -> None:
    # A fresh interpreter, so modules other tests imported do not count.
    script = (
        "import sys, team_4_issue_tracker_trello\n"
        "loaded = sorted({m.split('.')[0] for m in sys.modules}"
        " & {'app', 'fastapi', 'opentelemetry', 'starlette'})\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(PACKAGE_DIR.parent)},
        cwd=PACKAGE_DIR.parent,
    )
    assert result.stdout.strip() == ""
