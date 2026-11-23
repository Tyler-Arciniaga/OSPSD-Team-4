"""Which modules may know about which packages.

- No module imports a provider implementation or SDK. Implementations are
  reached only through the *_PROVIDER import paths, which config.py resolves
  with importlib and factories.py calls.
- Partner contract types (calendar, storage, and the tracker's interface and
  models) are known only to the adapters, the factories and the fakes. The
  orchestrator, planner, doc and endpoint see StepError and plain values.
  The HW1 error model (`issue_tracker_contract.errors`) is shared by all.
- The planner and doc renderer are pure: standard library only.
- Only store.py talks to the database; everything else uses StateStore.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / "src" / "ospsd_team4_product"

IMPLEMENTATIONS = {
    "team5_calendar",
    "dropbox_client_impl",
    "cloud_storage_client_api",
    "team_4_issue_tracker_trello",
}
SDKS = {
    "dropbox",
    "google",
    "googleapiclient",
    "google_auth_oauthlib",
    "google_auth_httplib2",
    "httplib2",
    "requests",
    "requests_oauthlib",
    "httpx",
    "httpx2",
    "dotenv",
}
PARTNER_CONTRACTS = {
    "calendar_contract",
    "cloud_storage_api",
    "issue_tracker_contract.interface",
    "issue_tracker_contract.models",
}
# Modules (relative to the package) allowed to import partner contracts.
CONTRACT_AWARE = ("adapters/", "fakes/", "factories.py")
PURE = {"planner.py", "doc.py"}
STDLIB_FOR_PURE = {"re", "collections", "dataclasses", "datetime", "typing", "zoneinfo"}


def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    """Every module named by an import statement, in full and by prefix."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found = [node.module]
        else:
            continue
        for name in found:
            parts = name.split(".")
            names.update(".".join(parts[: i + 1]) for i in range(len(parts)))
    return names


def relative(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


@pytest.mark.parametrize("path", modules(), ids=relative)
def test_no_module_imports_an_implementation_or_sdk(path: Path) -> None:
    assert imported_names(path) & (IMPLEMENTATIONS | SDKS) == set()


@pytest.mark.parametrize("path", modules(), ids=relative)
def test_only_the_adapter_layer_knows_partner_contracts(path: Path) -> None:
    if relative(path).startswith(CONTRACT_AWARE):
        return
    assert imported_names(path) & PARTNER_CONTRACTS == set()


@pytest.mark.parametrize("name", sorted(PURE))
def test_planner_and_doc_are_pure(name: str) -> None:
    roots = {n.split(".")[0] for n in imported_names(PACKAGE / name)}
    assert roots <= STDLIB_FOR_PURE | {"ospsd_team4_product"}
    assert {
        n
        for n in imported_names(PACKAGE / name)
        if n.startswith("ospsd_team4_product.")
    } <= {"ospsd_team4_product.planner"}


DATABASE_DRIVERS = {"psycopg", "psycopg_pool", "sqlite3"}


@pytest.mark.parametrize("path", modules(), ids=relative)
def test_only_the_store_knows_the_database(path: Path) -> None:
    if relative(path) == "store.py":
        return
    assert imported_names(path) & DATABASE_DRIVERS == set()


def test_only_tests_and_configuration_select_the_fakes() -> None:
    for path in modules():
        if relative(path).startswith("fakes/"):
            continue
        assert "ospsd_team4_product.fakes" not in imported_names(path), relative(path)


def test_importing_the_app_loads_no_implementation() -> None:
    """At runtime too: only configuration brings a provider in."""
    script = (
        "import sys, ospsd_team4_product.main\n"
        f"loaded = sorted(m for m in sys.modules if m.split('.')[0] in {sorted(IMPLEMENTATIONS | SDKS)!r})\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
