"""Tracker adapter: issues for the planner and error mapping for list_issues."""

import pytest
from issue_tracker_contract.errors import ErrorCode, ServiceError
from issue_tracker_contract.models import MAX_LIST_LIMIT, Issue

from ospsd_team4_product.adapters.tracker import IssueList, TrackerAdapter
from ospsd_team4_product.errors import StepError
from ospsd_team4_product.fakes import InMemoryIssueProvider
from ospsd_team4_product.planner import PlanIssue


def test_lists_issues_for_the_planner() -> None:
    provider = InMemoryIssueProvider(
        [Issue(id="b", title="B"), Issue(id="a", title="A")]
    )

    assert TrackerAdapter(provider).list_issues() == IssueList(
        issues=[PlanIssue(id="b", title="B"), PlanIssue(id="a", title="A")],
        truncated=False,
    )


def test_asks_for_the_contract_maximum_and_reports_truncation() -> None:
    provider = InMemoryIssueProvider(
        [Issue(id=str(i), title="t") for i in range(MAX_LIST_LIMIT + 30)]
    )

    listed = TrackerAdapter(provider).list_issues()

    assert [issue.id for issue in listed.issues] == [
        str(i) for i in range(MAX_LIST_LIMIT)
    ]
    assert listed.truncated is True


@pytest.mark.parametrize(("count", "truncated"), [(99, False), (100, True)])
def test_a_full_page_is_reported_as_truncated(count: int, truncated: bool) -> None:
    """Exactly 100 counts: the contract cannot tell it from more than 100."""
    provider = InMemoryIssueProvider(
        [Issue(id=str(i), title="t") for i in range(count)]
    )

    assert TrackerAdapter(provider).list_issues().truncated is truncated


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ErrorCode.PROVIDER_ERROR, True),
        (ErrorCode.ACCESS_DENIED, False),
        (ErrorCode.SERVICE_NOT_CONFIGURED, False),
    ],
)
def test_service_errors_keep_their_code(code: ErrorCode, retryable: bool) -> None:
    provider = InMemoryIssueProvider(
        always_fail=ServiceError(code, "The issue tracker is unavailable", cause="x")
    )

    with pytest.raises(StepError) as raised:
        TrackerAdapter(provider).list_issues()

    assert raised.value.step == "list_issues"
    assert raised.value.code == code
    assert raised.value.message == "The issue tracker is unavailable"
    assert raised.value.retryable is retryable
    assert raised.value.may_have_applied is False


def test_unexpected_exception_is_a_provider_error_without_its_message() -> None:
    provider = InMemoryIssueProvider(always_fail=RuntimeError("token=abc"))

    with pytest.raises(StepError) as raised:
        TrackerAdapter(provider).list_issues()

    assert raised.value.code == ErrorCode.PROVIDER_ERROR
    assert "token" not in raised.value.message
