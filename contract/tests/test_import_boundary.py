"""The contract package must not depend on the service or any provider.

Dependencies point one way: `app/` imports `issue_tracker_contract`, never the
reverse. The contract may use only the standard library, pydantic, and its
own modules, so it carries no web framework, provider SDK, or service code.
"""

import ast
import sys
from pathlib import Path

PACKAGE_NAME = "issue_tracker_contract"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / PACKAGE_NAME
ALLOWED_THIRD_PARTY = frozenset({"pydantic", "pydantic_core", "typing_extensions"})


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
    assert {"__init__.py", "errors.py", "interface.py", "models.py"} <= names


def test_contract_imports_nothing_from_service_or_providers() -> None:
    violations = {
        str(path.relative_to(PACKAGE_DIR)): bad
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if (bad := forbidden_imports(path.read_text(encoding="utf-8")))
    }
    assert violations == {}, (
        "issue_tracker_contract must not import service, framework, or provider "
        f"code: {violations}"
    )


def test_boundary_check_rejects_service_and_framework_imports() -> None:
    source = """
import json
from pydantic import BaseModel
from issue_tracker_contract.models import Issue
from . import errors
from app.providers.trello_provider import TrelloIssueProvider
import app.errors
from fastapi import FastAPI
"""
    assert forbidden_imports(source) == [
        "app.providers.trello_provider",
        "app.errors",
        "fastapi",
    ]
