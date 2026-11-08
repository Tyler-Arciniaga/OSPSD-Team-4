"""Linear adapter behavior that no single endpoint shows."""

import httpx
import pytest

from app.interface import ClosableIssueProvider, IdempotentIssueProvider
from app.providers.linear_provider import LinearIssueProvider
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import IssueUpdate
from tests.linear.fake_linear import (
    AUTHENTICATION_BODY,
    ISSUE_ID,
    TITLE,
    FakeLinear,
    Handler,
    fail_with,
    graphql_error,
    issue_body,
    respond,
    simulated_provider,
)


def test_linear_implements_the_provider_protocols() -> None:
    linear = LinearIssueProvider()
    try:
        assert isinstance(linear, IssueProvider)
        assert isinstance(linear, ClosableIssueProvider)
        assert not isinstance(linear, IdempotentIssueProvider)
    finally:
        linear.close()


@pytest.mark.parametrize("fresh", [False, True])
def test_every_read_queries_linear(linear: FakeLinear, fresh: bool) -> None:
    # The adapter saves no results, so `fresh` has nothing to bypass.
    linear.handler = respond(200, issue_body())
    adapter = simulated_provider(linear)

    adapter.get_issue(ISSUE_ID, fresh=fresh)
    adapter.get_issue(ISSUE_ID, fresh=fresh)

    assert len(linear.requests) == 2


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_unimplemented_operations_refuse_without_a_request(
    linear: FakeLinear, operation: str
) -> None:
    adapter = simulated_provider(linear)

    with pytest.raises(ServiceError) as exc:
        if operation == "update":
            adapter.update_issue(ISSUE_ID, IssueUpdate(title=TITLE))
        else:
            adapter.delete_issue(ISSUE_ID)

    assert exc.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert exc.value.cause == "operation_not_implemented"
    assert not exc.value.provider_involved
    assert not exc.value.may_have_applied
    assert linear.requests == []


def test_close_closes_the_http_client(linear: FakeLinear) -> None:
    linear.handler = respond(200, issue_body())
    adapter = simulated_provider(linear)
    adapter.close()

    # A closed client cannot send; the adapter must not hide that as success.
    with pytest.raises(RuntimeError):
        adapter.get_issue(ISSUE_ID)
    assert linear.requests == []


def test_linear_ping_asks_for_the_viewer(linear: FakeLinear) -> None:
    linear.handler = respond(200, {"data": {"viewer": {"id": "user-id"}}})

    assert simulated_provider(linear).ping() is True
    assert len(linear.requests) == 1
    assert "viewer" in linear.payloads[0]["query"]


@pytest.mark.parametrize(
    "handler",
    [
        respond(401, AUTHENTICATION_BODY),
        respond(503, None),
        respond(200, graphql_error("INTERNAL_ERROR")),
        respond(200, {"data": {"viewer": None}}),
        fail_with(httpx.ConnectTimeout("timed out")),
        fail_with(httpx.ConnectError("refused")),
    ],
)
def test_linear_ping_reports_failures_as_false(
    linear: FakeLinear, handler: Handler
) -> None:
    linear.handler = handler

    assert simulated_provider(linear).ping() is False


def test_linear_ping_without_configuration_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, linear: FakeLinear
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY")

    assert simulated_provider(linear).ping() is False
    assert linear.requests == []
