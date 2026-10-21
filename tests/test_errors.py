import json
import logging
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from app import main, provider
from app.dependencies import get_issue_provider
from app.errors import ErrorCode, ServiceError
from app.interface import IssueProvider
from app.models import Issue, IssueUpdate

KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"


# Keep server errors as responses so the 500 contract can be checked.
@pytest.fixture
def raise_server_exceptions() -> bool:
    return False


class ControlledProvider:
    """A provider whose every operation returns or raises a chosen outcome."""

    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome
        self.calls = 0

    def _run(self, issue: Issue) -> Issue:
        self.calls += 1
        if self.outcome is not None:
            raise self.outcome
        return issue

    def get_issue(self, issue_id: str) -> Issue:
        return self._run(Issue(id=issue_id, title="Controlled issue"))

    def update_issue(self, issue_id: str, changes: IssueUpdate) -> Issue:
        return self._run(Issue(id=issue_id, title=changes.title or ""))

    def delete_issue(self, issue_id: str) -> None:
        self._run(Issue(id=issue_id, title=""))

    def create_issue(self, title: str) -> Issue:
        return self._run(Issue(id=KNOWN_ISSUE_ID, title=title))


def use_provider(monkeypatch: pytest.MonkeyPatch, supplied: ControlledProvider) -> None:
    def supply_provider() -> IssueProvider:
        return supplied

    monkeypatch.setitem(
        main.app.dependency_overrides, get_issue_provider, supply_provider
    )


def failure_logs(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.errors"
    ]


# The four cases the error contract must cover, using a controlled provider.


def test_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ControlledProvider())

    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 200
    assert response.json() == {"id": KNOWN_ISSUE_ID, "title": "Controlled issue"}


def test_invalid_input(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    supplied = ControlledProvider()
    use_provider(monkeypatch, supplied)

    response = client.post("/issues", json={"title": "", "extra_input": "invalid"})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_input",
            "message": "Request body is invalid",
            "details": [
                {
                    "field": "title",
                    "message": "String should have at least 1 character",
                },
                {"field": "extra_input", "message": "Extra inputs are not permitted"},
            ],
        }
    }
    assert supplied.calls == 0


def test_missing_resource(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(
        monkeypatch,
        ControlledProvider(
            ServiceError(ErrorCode.NOT_FOUND, "Issue not found", provider_involved=True)
        ),
    )

    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Issue not found"}
    }


def test_provider_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(
        monkeypatch,
        ControlledProvider(
            ServiceError(
                ErrorCode.PROVIDER_ERROR,
                "The issue tracker is currently unavailable",
                provider_involved=True,
            )
        ),
    )

    response = client.post("/issues", json={"title": "New issue"})

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "The issue tracker is currently unavailable",
        }
    }


# Further classes of failure.


def test_access_denied(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(
        monkeypatch,
        ControlledProvider(
            ServiceError(
                ErrorCode.ACCESS_DENIED,
                "The issue tracker denied access to this issue",
                provider_involved=True,
            )
        ),
    )

    response = client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "access_denied",
            "message": "The issue tracker denied access to this issue",
        }
    }


def test_unexpected_exception_hides_internals(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_provider(monkeypatch, ControlledProvider(RuntimeError("private bug details")))

    response = client.patch(f"/issues/{KNOWN_ISSUE_ID}", json={"title": "Renamed"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert failure_logs(caplog) == [
        {
            "level": "ERROR",
            "operation": "update_issue",
            "error": "internal_error",
            "status": 500,
            "provider_involved": False,
            "cause": "RuntimeError",
            "write_outcome": "unknown",
        }
    ]
    # A bug is logged with its stack trace so it can be found.
    [record] = [r for r in caplog.records if r.name == "app.errors"]
    assert record.levelno == logging.ERROR
    assert record.exc_info


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Resource not found"}
    }


# Logging.


def test_failure_is_logged_once_without_stack_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_provider(
        monkeypatch,
        ControlledProvider(
            ServiceError(
                ErrorCode.NOT_FOUND,
                "Issue not found",
                provider_involved=True,
                cause="provider_status=404",
            )
        ),
    )

    client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert failure_logs(caplog) == [
        {
            "level": "WARNING",
            "operation": "get_issue",
            "error": "not_found",
            "status": 404,
            "provider_involved": True,
            "cause": "provider_status=404",
            "write_outcome": None,
        }
    ]
    [record] = [r for r in caplog.records if r.name == "app.errors"]
    assert not record.exc_info


def test_rejection_before_provider_is_logged_as_not_involving_it(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.get("/issues/does-not-exist")

    assert failure_logs(caplog) == [
        {
            "level": "WARNING",
            "operation": "get_issue",
            "error": "not_found",
            "status": 404,
            "provider_involved": False,
            "cause": "malformed_issue_id",
            "write_outcome": None,
        }
    ]


# Each failure deliberately carries credentials and provider details in its
# URL or message, whether or not real requests would, to prove that none of
# them reach the response or the logs.
@pytest.mark.parametrize(
    ("error", "level", "code", "status", "cause"),
    [
        (
            HTTPError(
                "https://api.trello.com/1/cards?key=test-key&token=test-token",
                401,
                "invalid token test-token",
                Message(),
                None,
            ),
            "WARNING",
            "access_denied",
            403,
            "provider_status=401",
        ),
        (
            HTTPError(
                "https://api.trello.com/?token=test-token", 500, "", Message(), None
            ),
            "ERROR",
            "provider_error",
            502,
            "provider_status=500",
        ),
        (
            URLError("cannot reach https://api.trello.com/?token=test-token"),
            "ERROR",
            "provider_error",
            502,
            "URLError",
        ),
        (
            TimeoutError("timed out for token=test-token"),
            "ERROR",
            "provider_error",
            502,
            "timeout",
        ),
    ],
)
def test_real_provider_failures_are_sanitized(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    level: str,
    code: str,
    status: int,
    cause: str,
) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "test-key")
    monkeypatch.setenv("TRELLO_TOKEN", "test-token")

    def fail(request: Request, timeout: int) -> None:
        raise error

    monkeypatch.setattr(provider, "urlopen", fail)

    with caplog.at_level(logging.DEBUG):
        response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    for text in (response.text, caplog.text):
        assert "test-token" not in text
        assert "test-key" not in text
        assert "trello" not in text.lower()
    assert failure_logs(caplog) == [
        {
            "level": level,
            "operation": "get_issue",
            "error": code,
            "status": status,
            "provider_involved": True,
            "cause": cause,
            "write_outcome": None,
        }
    ]


def test_missing_configuration_does_not_involve_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)

    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_not_configured",
            "message": "Issue tracker credentials not configured",
        }
    }
    assert failure_logs(caplog) == [
        {
            "level": "ERROR",
            "operation": "get_issue",
            "error": "service_not_configured",
            "status": 503,
            "provider_involved": False,
            "cause": "missing_credentials",
            "write_outcome": None,
        }
    ]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        # Sent, but no usable answer: the write may have been applied.
        (
            ServiceError(ErrorCode.PROVIDER_ERROR, "x", provider_involved=True),
            "unknown",
        ),
        # The provider refused the write.
        (ServiceError(ErrorCode.NOT_FOUND, "x", provider_involved=True), "not_applied"),
    ],
)
def test_write_failure_logs_whether_change_was_applied(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: ServiceError,
    expected: str,
) -> None:
    use_provider(monkeypatch, ControlledProvider(outcome))

    client.delete(f"/issues/{KNOWN_ISSUE_ID}")

    [record] = failure_logs(caplog)
    assert record["write_outcome"] == expected


def test_locally_rejected_write_logs_not_applied(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    supplied = ControlledProvider()
    use_provider(monkeypatch, supplied)

    client.post("/issues", json={"title": ""})

    [record] = failure_logs(caplog)
    assert record["write_outcome"] == "not_applied"
    assert supplied.calls == 0
