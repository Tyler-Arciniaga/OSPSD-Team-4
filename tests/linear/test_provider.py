"""Linear adapter behavior that no single endpoint shows."""

import pytest

from app.interface import ClosableIssueProvider, IdempotentIssueProvider
from app.providers.linear_provider import LinearIssueProvider
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.interface import IssueProvider
from issue_tracker_contract.models import IssueUpdate
from tests.linear.fake_linear import (
    ISSUE_ID,
    TITLE,
    FakeLinear,
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
