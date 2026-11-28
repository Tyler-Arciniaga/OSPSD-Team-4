"""The TUI reaches the product only over HTTP (issue #88, Boundary).

No TUI module may import the product service, a database driver, or any
provider or contract package. Its runtime dependencies are textual and httpx
only.
"""

import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "focus_day_tui"

FORBIDDEN = {
    "ospsd_team4_product",
    "sqlite3",
    "team_4_issue_tracker_trello",
    "team5_calendar",
    "dropbox_client_impl",
    "team1_dropbox",
    "calendar_contract",
    "cloud_storage_api",
    "issue_tracker_contract",
}
RUNTIME_DEPENDENCIES = {"textual", "httpx"}


def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_module_imports_the_product_or_a_provider(path: Path) -> None:
    assert imported_roots(path) & FORBIDDEN == set()


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_modules_import_only_the_stdlib_and_declared_dependencies(
    path: Path,
) -> None:
    allowed = sys.stdlib_module_names | RUNTIME_DEPENDENCIES | {"focus_day_tui"}
    assert imported_roots(path) - allowed == set()


def test_runtime_dependencies_are_textual_and_httpx_only() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    names = {
        requirement.split(">")[0].split("=")[0].split("[")[0].strip()
        for requirement in project["dependencies"]
    }
    assert names == RUNTIME_DEPENDENCIES


def test_the_checker_catches_a_forbidden_import(tmp_path: Path) -> None:
    module = tmp_path / "bad.py"
    module.write_text("import sqlite3\nfrom ospsd_team4_product.main import app\n")
    assert imported_roots(module) & FORBIDDEN == {"sqlite3", "ospsd_team4_product"}
