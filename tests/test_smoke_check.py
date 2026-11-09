"""scripts/smoke_check.sh against the controlled service, healthy and broken.

Each case starts the real app under uvicorn with the in-memory provider
(tests/controlled/server.py), optionally with an injected fault, and runs
the smoke script against it exactly as the release workflow does.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "smoke_check.sh"
COMMIT = "e" * 40


@dataclass(frozen=True)
class Server:
    url: str


StartServer = Callable[..., Server]


@dataclass(frozen=True)
class SmokeResult:
    status: int
    output: str
    summary: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean_environment() -> dict[str, str]:
    # Nothing from this test process's own configuration or pytest run.
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TRELLO_", "LINEAR_", "PYTEST_", "CONFORMANCE_"))
        and name not in ("ISSUE_PROVIDER", "IDEMPOTENCY_DB_PATH", "GIT_SHA")
    }


@pytest.fixture(scope="module")
def start_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[StartServer]:
    processes: list[subprocess.Popen[bytes]] = []

    def start(fault: str | None = None) -> Server:
        port = _free_port()
        env = _clean_environment()
        env["GIT_SHA"] = COMMIT
        env["IDEMPOTENCY_DB_PATH"] = str(
            tmp_path_factory.mktemp("server") / "keys.sqlite3"
        )
        if fault:
            env["CONTROLLED_FAULT"] = fault
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.controlled.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    return Server(url)
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("the controlled server did not start")

    yield start

    for process in processes:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="module")
def healthy(start_server: StartServer) -> Server:
    return start_server()


def run_smoke(
    url: str, commit: str, tmp_path: Path, *, ready_timeout: str = "20"
) -> SmokeResult:
    summary = tmp_path / "summary.md"
    summary.write_text("")
    env = _clean_environment()
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    env["SMOKE_READY_TIMEOUT"] = ready_timeout
    completed = subprocess.run(
        [str(SCRIPT), url, commit],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    return SmokeResult(
        completed.returncode, completed.stdout + completed.stderr, summary.read_text()
    )


def test_working_release_passes(healthy: Server, tmp_path: Path) -> None:
    result = run_smoke(healthy.url, COMMIT, tmp_path)

    assert result.status == 0, result.output
    assert "## Smoke check: passed" in result.summary
    assert "passed" in result.summary
    assert "Leftover issues" not in result.summary


def test_contract_break_fails_and_names_the_tests(
    start_server: StartServer, tmp_path: Path
) -> None:
    broken = start_server("wrong_title")

    result = run_smoke(broken.url, COMMIT, tmp_path)

    assert result.status != 0
    assert "## Smoke check: FAILED" in result.summary
    assert "Failing tests:" in result.summary
    assert "test_create_returns_the_new_issue_and_its_location" in result.summary


def test_cleanup_failure_passes_but_lists_leftover_issues(
    start_server: StartServer, tmp_path: Path
) -> None:
    leaky = start_server("delete_fails")

    result = run_smoke(leaky.url, COMMIT, tmp_path)

    assert result.status == 0, result.output
    assert "## Smoke check: passed" in result.summary
    assert "Leftover issues: remove by hand" in result.summary
    listed = [
        line for line in result.summary.splitlines() if line.endswith("(HTTP 502)`")
    ]
    assert listed
    # Each leftover is a 24-character ID from the in-memory provider.
    assert all(len(line.split("`")[1].split(" ")[0]) == 24 for line in listed)


def test_wrong_commit_fails_before_running_the_suite(
    healthy: Server, tmp_path: Path
) -> None:
    result = run_smoke(healthy.url, "f" * 40, tmp_path, ready_timeout="2")

    assert result.status != 0
    assert "not ready with commit" in result.summary
    assert "conformance suite did not run" in result.summary
    assert "contract/tests/conformance" not in result.output


def test_unreachable_service_fails(tmp_path: Path) -> None:
    result = run_smoke(
        f"http://127.0.0.1:{_free_port()}", COMMIT, tmp_path, ready_timeout="2"
    )

    assert result.status != 0
    assert "## Smoke check: FAILED" in result.summary
