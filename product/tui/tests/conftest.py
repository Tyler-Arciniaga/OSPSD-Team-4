"""A fake product API on `httpx.MockTransport`, and the app wired to it."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from textual.pilot import Pilot

from focus_day_tui.app import FocusDayApp, FocusDayScreen
from tests.bodies import DAY, ZONE

API_URL = "http://api.test"
Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeApi:
    """Answers `/readyz` with 200 and each focus-day POST from a queue.

    Every request is recorded; `posts` are the focus-day ones.
    """

    responses: list[Handler | httpx.Response] = field(default_factory=list)
    ready: Handler | httpx.Response = field(
        default_factory=lambda: httpx.Response(200, json={"status": "ready"})
    )
    requests: list[httpx.Request] = field(default_factory=list)

    def reply(self, status: int, body: object, **headers: str) -> None:
        self.responses.append(httpx.Response(status, json=body, headers=headers))

    @property
    def posts(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == "/v1/focus-day"
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/readyz":
            answer = self.ready
        else:
            assert self.responses, f"unexpected request {request.url.path}"
            answer = self.responses.pop(0)
        return answer if isinstance(answer, httpx.Response) else answer(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def make_app(api: FakeApi, state_dir: Path) -> Callable[..., FocusDayApp]:
    def make(**overrides: Any) -> FocusDayApp:
        options: dict[str, Any] = {
            "api_url": API_URL,
            "timeout": 5.0,
            "state_dir": state_dir,
            "transport": api.transport,
            "today": date.fromisoformat(DAY),
            "system_zone": ZONE,
        }
        options.update(overrides)
        return FocusDayApp(**options)

    return make


def screen_of(app: FocusDayApp) -> FocusDayScreen:
    screen = app.screen
    assert isinstance(screen, FocusDayScreen)
    return screen


def text(app: FocusDayApp, selector: str) -> str:
    """What a widget on the Focus day screen shows, without markup."""
    return str(screen_of(app).query_one(selector).render())


async def settle(pilot: Pilot[None]) -> None:
    """Let the pending messages start their workers, the workers finish, and
    the screen catch up."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
