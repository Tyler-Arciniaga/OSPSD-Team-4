"""Several service instances share one state database (N >= 1).

Each test starts two real service processes (uvicorn, with the in-memory
providers) on different ports, both pointed at the TEST_STATE_DB_URL
database, the way docker-compose.yml runs them. Each process has its own
providers, its own connection pool and no shared memory, so anything one
instance can see that another saved came through the database.

Until the read endpoints exist (#86, #87), the plan an instance saved is
read through a third, independent store, standing in for instance B's.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from ospsd_team4_product.store import PostgresStateStore
from tests.test_focus_day import FAKES, REQUEST, confirmed, replay_of

PRODUCT = Path(__file__).parents[1]
DAY = date(2026, 11, 18)
STARTUP_SECONDS = 30


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class Instance:
    process: subprocess.Popen[bytes]
    client: httpx.Client

    def focus_day(self, body: dict[str, Any]) -> Any:
        return self.client.post("/v1/focus-day", json=body)

    def book(self, body: dict[str, Any]) -> Any:
        """Propose, then confirm the proposed plan unchanged."""
        plan = self.focus_day(body).json()["plan"]
        return self.focus_day(confirmed(body, plan))


def start(url: str) -> Instance:
    port = free_port()
    environ = os.environ | FAKES | {"STATE_DB_URL": url}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ospsd_team4_product.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PRODUCT,
        env=environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"instance on port {port} exited at startup")
        try:
            if client.get("/readyz").status_code == 200:
                return Instance(process, client)
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    process.kill()
    raise AssertionError(f"instance on port {port} did not become ready")


@pytest.fixture
def instances(postgres_url: str) -> Iterator[tuple[Instance, Instance]]:
    started: list[Instance] = []
    try:
        started.append(start(postgres_url))
        started.append(start(postgres_url))
        yield started[0], started[1]
    finally:
        for instance in started:
            instance.client.close()
            instance.process.terminate()
            instance.process.wait(timeout=10)


@pytest.fixture
def shared(postgres_url: str) -> Iterator[PostgresStateStore]:
    store = PostgresStateStore.connect(postgres_url)
    yield store
    store.close()


def test_a_plan_saved_through_one_instance_is_visible_to_another(
    instances: tuple[Instance, Instance], shared: PostgresStateStore
) -> None:
    a, b = instances

    body_a = a.book(REQUEST | {"assignee": "alice"}).json()
    body_b = b.book(REQUEST | {"assignee": "bob"}).json()

    assert body_a["status"] == body_b["status"] == "ok"
    for assignee, body in (("alice", body_a), ("bob", body_b)):
        plan = shared.get_plan(assignee, DAY)
        assert plan is not None
        assert [block.event_id for block in plan] == list(body["created"].values())
    matches = shared.find_plans_with_issue("demo-1")
    assert {match.assignee for match in matches} == {"alice", "bob"}


def test_concurrent_saves_through_both_instances_leave_one_whole_plan(
    instances: tuple[Instance, Instance], shared: PostgresStateStore
) -> None:
    a, b = instances
    # A repeated run for the same day must be a replay (check_doc, F5), so
    # the concurrent saves are replays of one run, through both instances.
    first = a.book(REQUEST).json()
    replay = replay_of(first)

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda i: (a, b)[i % 2].focus_day(replay), range(10)))

    assert [response.status_code for response in responses] == [200] * 10
    plan = shared.get_plan("tyler", DAY)
    assert plan is not None
    assert [block.issue_id for block in plan] == ["demo-1", "demo-2", "demo-3"]
    # Every block comes from the same response: one writer won, whole.
    winners = [r.json()["created"] for r in responses]
    assert {block.issue_id: block.event_id for block in plan} in winners


def test_a_plan_proposed_on_one_instance_can_be_confirmed_on_another(
    instances: tuple[Instance, Instance], shared: PostgresStateStore
) -> None:
    """Proposals keep no server-side state, so any instance can confirm."""
    a, b = instances

    proposal = a.focus_day(REQUEST).json()
    body = b.focus_day(confirmed(REQUEST, proposal["plan"])).json()

    assert proposal["status"] == "proposed"
    assert body["status"] == "ok"
    assert body["plan"] == proposal["plan"]
    plan = shared.get_plan("tyler", DAY)
    assert plan is not None
    assert [block.event_id for block in plan] == list(body["created"].values())
