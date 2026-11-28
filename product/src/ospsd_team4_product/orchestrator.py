"""The focus-day operation: propose a plan, then book it once approved.

Propose (`confirm` false, the default) runs `validate`, `check_doc`,
`list_issues` and `plan`, and returns `status: proposed` with the plan. It
only reads: nothing is booked, written or saved, and the later steps are
`skipped`.

Confirm (`confirm: true` with `approved_plan`, the proposal's `plan` sent
back unchanged) runs the same reads, then compares the recomputed plan with
`approved_plan`. If they differ (the tracker changed, or the caller edited
the plan), the `plan` step fails with 412 and nothing is written; the error
body carries the new plan to show the owner. If they match, the later steps
book exactly what the owner approved. Nothing is kept between the two calls:
the approved plan travels in the request, so any caller (TUI, agent, curl)
gets the same rule.

Steps, in order, each run at most once per request:

0. `validate`: the whole request, before any provider is called.
1. `check_doc`: a run without `replay` stops with 412 if the doc for this
   assignee and day already exists, so sending the same request twice does
   not book a second set of events (F5).
2. `list_issues`: every issue in the configured tracker collection, up to
   100; `issues_truncated` reports when the limit was reached (F6).
3. `plan`: the pure planner; no I/O. On confirm, also the approval check.
4. `create_events`: one calendar event per block, in block order. Fails fast
   on the first calendar error; an uncertain create is never retried.
5. `write_standup_doc`: upsert the doc, only once every event exists.
6. `save_plan`: save the plan, with each block's event ID, as the assignee's
   approved plan for the day (store.py). Only a run whose other steps all
   succeeded saves anything, so the store never holds a draft. Saving
   replaces any earlier plan for that day, so a replay can repeat it.

All input is validated in the `validate` step, before any provider is
called, so invalid input never has a side effect.

Replay (only with `confirm: true`): a retry of a confirmed run sends back
the earlier response's `plan` and `created`. The plan is not recomputed
(`check_doc`, `list_issues` and `plan` are skipped): recomputing could move
blocks, for example once the caller lists our own events as busy.
For each block with an event ID in `created`, `get` confirms the event still
exists; only blocks without an event, or whose event is NOT_FOUND, get a new
one. The doc write is then repeated, which is safe because the same plan
gives the same bytes, and the plan is saved again.

Only the adapters know provider types. This module sees StepError and
plain values.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, time

from issue_tracker_contract.errors import ErrorCode, ErrorDetail

from ospsd_team4_product.adapters.calendar import CalendarAdapter
from ospsd_team4_product.adapters.storage import StorageAdapter
from ospsd_team4_product.adapters.tracker import TrackerAdapter
from ospsd_team4_product.config import ProductConfig
from ospsd_team4_product.doc import doc_path, render_doc
from ospsd_team4_product.errors import STEPS, Step, StepError
from ospsd_team4_product.models import (
    DocModel,
    FocusDayErrorBody,
    FocusDayErrorResponse,
    FocusDayProposal,
    FocusDayRequest,
    FocusDayResponse,
    PlanModel,
    StepModel,
    StepStatus,
)
from ospsd_team4_product.planner import (
    BusyInterval,
    Plan,
    PlanInputError,
    PlanIssue,
    parse_interval,
    plan_day,
)
from ospsd_team4_product.store import CalendarBlock, StateStore, StoreError

INVALID_REQUEST = "The request is invalid"
PLAN_CHANGED = "The plan changed since you reviewed it"
SAVE_FAILED = (
    "Your calendar blocks and standup doc are done, but the plan could not be"
    " saved. Retry with replay to save it."
)


@dataclass(frozen=True)
class Outcome:
    """The HTTP status and body of one focus-day request."""

    status_code: int
    body: FocusDayResponse | FocusDayProposal | FocusDayErrorResponse
    # The failed step's internal reason, for logs only (StepError.cause).
    cause: str | None = None


@dataclass(frozen=True)
class _Inputs:
    """The request with configuration defaults applied; all validated."""

    assignee: str
    day: date
    path: str
    timezone: str
    window_start: time
    window_end: time
    busy: tuple[BusyInterval, ...]
    minutes: int
    confirm: bool
    approved_plan: PlanModel | None
    replay_plan: Plan | None
    replay_created: dict[str, str]

    def plan(self, issues: Sequence[PlanIssue]) -> Plan:
        """Run the planner; raises PlanInputError if the input is invalid."""
        return plan_day(
            issues,
            day=self.day,
            timezone=self.timezone,
            window_start=self.window_start,
            window_end=self.window_end,
            busy=self.busy,
            minutes=self.minutes,
        )


@dataclass
class _Progress:
    """What has happened so far; becomes the response either way."""

    statuses: dict[Step, StepStatus] = field(
        default_factory=lambda: dict.fromkeys(STEPS, "skipped")
    )
    plan: Plan | None = None
    created: dict[str, str] = field(default_factory=dict)
    issues_truncated: bool | None = None

    def done(self, step: Step) -> None:
        self.statuses[step] = "ok"

    def proposal(self) -> Outcome:
        assert self.plan is not None
        return Outcome(
            200,
            FocusDayProposal(
                status="proposed",
                steps=[StepModel(name=s, status=v) for s, v in self.statuses.items()],
                plan=PlanModel.of(self.plan),
                issues_truncated=self.issues_truncated,
            ),
        )

    def success(self, doc: DocModel) -> Outcome:
        assert self.plan is not None
        return Outcome(
            200,
            FocusDayResponse(
                status="ok",
                steps=[StepModel(name=s, status=v) for s, v in self.statuses.items()],
                plan=PlanModel.of(self.plan),
                created=self.created,
                doc=doc,
                issues_truncated=self.issues_truncated,
            ),
        )

    def failure(self, exc: StepError) -> Outcome:
        error = FocusDayErrorBody(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            step=exc.step,
            retryable=exc.retryable,
            may_have_applied=exc.may_have_applied,
        )
        self.statuses[exc.step] = "failed"
        steps = [
            StepModel(
                name=step, status=status, error=error if step == exc.step else None
            )
            for step, status in self.statuses.items()
        ]
        return Outcome(
            exc.status,
            FocusDayErrorResponse(
                status="partial" if self.created else "failed",
                steps=steps,
                plan=PlanModel.of(self.plan) if self.plan is not None else None,
                created=self.created,
                issues_truncated=self.issues_truncated,
                failed_step=exc.step,
                error=error,
            ),
            cause=exc.cause,
        )


def _invalid(exc: PlanInputError, step: Step = "validate") -> StepError:
    return StepError(
        step,
        ErrorCode.INVALID_INPUT,
        INVALID_REQUEST,
        details=[ErrorDetail(field=exc.field, message=exc.message)],
        cause="invalid_input",
    )


def rejected_request(details: list[ErrorDetail]) -> Outcome:
    """The outcome for a body that failed schema validation (`validate` step)."""
    return _Progress().failure(
        StepError(
            "validate",
            ErrorCode.INVALID_INPUT,
            INVALID_REQUEST,
            details=details,
            cause="invalid_input",
        )
    )


def _check_approval_fields(request: FocusDayRequest) -> None:
    """How `confirm`, `approved_plan` and `replay` may combine; raise
    PlanInputError otherwise.

    Only a confirmed request writes anything, so a request that looks like
    it means to book (it carries `approved_plan` or `replay`) but is not
    confirmed is rejected rather than quietly treated as a proposal.
    """
    if request.replay is not None:
        if not request.confirm:
            raise PlanInputError("confirm", "must be true to replay a booked run")
        if request.approved_plan is not None:
            raise PlanInputError(
                "approved_plan", "must not be sent with replay; replay.plan is used"
            )
    elif request.confirm and request.approved_plan is None:
        raise PlanInputError(
            "approved_plan", "is required with confirm: send the proposal's plan"
        )
    elif not request.confirm and request.approved_plan is not None:
        raise PlanInputError(
            "approved_plan", "is only used with confirm: true; nothing was booked"
        )


def _replay_plan(request: FocusDayRequest) -> tuple[Plan | None, dict[str, str]]:
    """Check a replayed plan by the planner's own rules; raise PlanInputError."""
    if request.replay is None:
        return None, {}
    plan = request.replay.plan.to_plan()
    issue_ids: set[str] = set()
    for index, block in enumerate(plan.blocks):
        parse_interval(block.start, block.end, f"replay.plan.blocks[{index}]")
        if block.issue_id in issue_ids:
            raise PlanInputError(
                f"replay.plan.blocks[{index}].issue_id",
                "appears in more than one block",
            )
        issue_ids.add(block.issue_id)
    for issue_id in request.replay.created:
        if issue_id not in issue_ids:
            raise PlanInputError(
                "replay.created", "names an issue with no block in replay.plan"
            )
    # Replay skips the approval check, so it only resumes a run that booked
    # something; with no event yet, propose and confirm again. A plan with
    # no blocks books no event, so its doc and save can still be replayed.
    if plan.blocks and not request.replay.created:
        raise PlanInputError(
            "replay.created",
            "is empty, so nothing was booked: confirm the plan instead of replaying",
        )
    return plan, dict(request.replay.created)


def _check_approved(plan: Plan, approved: PlanModel | None) -> None:
    """Fail the `plan` step unless `plan` is exactly the approved plan.

    The whole plan is compared, blocks and unscheduled issues in order: a
    moved, removed or added block, a new issue, or an edited title all mean
    the owner did not see what would be booked.
    """
    if PlanModel.of(plan) != approved:
        raise StepError(
            "plan",
            ErrorCode.PRECONDITION_FAILED,
            PLAN_CHANGED,
            cause="plan_changed",
        )


class FocusDay:
    def __init__(
        self,
        *,
        tracker: TrackerAdapter,
        calendar: CalendarAdapter,
        storage: StorageAdapter,
        store: StateStore,
        config: ProductConfig,
    ) -> None:
        self._tracker = tracker
        self._calendar = calendar
        self._storage = storage
        self._store = store
        self._config = config

    def run(self, request: FocusDayRequest) -> Outcome:
        progress = _Progress()
        try:
            inputs = self._inputs(request)
            progress.done("validate")
            if inputs.replay_plan is None:
                self._storage.check_doc_absent(inputs.path)
                progress.done("check_doc")
                listed = self._tracker.list_issues()
                progress.issues_truncated = listed.truncated
                progress.done("list_issues")
                progress.plan = self._plan(listed.issues, inputs)
                if not inputs.confirm:
                    progress.done("plan")
                    return progress.proposal()
                _check_approved(progress.plan, inputs.approved_plan)
                progress.done("plan")
            else:
                progress.plan = inputs.replay_plan
                progress.created = inputs.replay_created
            self._create_events(progress.plan, progress.created)
            progress.done("create_events")
            stored = self._storage.write_doc(
                inputs.path,
                render_doc(
                    progress.plan,
                    day=inputs.day,
                    assignee=inputs.assignee,
                    timezone=inputs.timezone,
                ),
            )
            progress.done("write_standup_doc")
            self._save_plan(inputs, progress.plan, progress.created)
            progress.done("save_plan")
        except StepError as exc:
            return progress.failure(exc)
        return progress.success(DocModel(path=stored.path, id=stored.id))

    def _inputs(self, request: FocusDayRequest) -> _Inputs:
        """Apply defaults and validate everything before any provider call."""
        config = self._config
        window = request.working_window
        day = request.parsed_day()
        try:
            inputs = _Inputs(
                assignee=request.assignee,
                day=day,
                path=doc_path(day, request.assignee),
                timezone=request.timezone or config.default_timezone,
                window_start=(
                    time.fromisoformat(window.start)
                    if window
                    else config.default_working_window.start
                ),
                window_end=(
                    time.fromisoformat(window.end)
                    if window
                    else config.default_working_window.end
                ),
                busy=tuple(
                    BusyInterval(start=b.start, end=b.end) for b in request.busy
                ),
                minutes=request.default_minutes or config.default_minutes,
                confirm=request.confirm,
                approved_plan=request.approved_plan,
                replay_plan=None,
                replay_created={},
            )
            # With no issues, the planner only validates its input.
            inputs.plan([])
            _check_approval_fields(request)
            replay_plan, replay_created = _replay_plan(request)
        except PlanInputError as exc:
            raise _invalid(exc) from None
        return replace(inputs, replay_plan=replay_plan, replay_created=replay_created)

    def _plan(self, issues: list[PlanIssue], inputs: _Inputs) -> Plan:
        # `validate` already ran the planner's input checks, so this is only
        # reached if the planner and those checks disagree.
        try:
            return inputs.plan(issues)
        except PlanInputError as exc:
            raise _invalid(exc, "plan") from None

    def _save_plan(self, inputs: _Inputs, plan: Plan, created: dict[str, str]) -> None:
        """Save the booked plan; every block has an event by now."""
        blocks = [
            CalendarBlock(
                issue_id=block.issue_id,
                title=block.title,
                start=block.start,
                end=block.end,
                event_id=created[block.issue_id],
            )
            for block in plan.blocks
        ]
        try:
            self._store.save_plan(inputs.assignee, inputs.day, blocks)
        except StoreError as exc:
            # Nothing was saved (the write is one transaction), and saving
            # replaces the day's plan, so a replay can simply repeat it.
            raise StepError(
                "save_plan",
                ErrorCode.INTERNAL_ERROR,
                SAVE_FAILED,
                retryable=True,
                cause=exc.cause or "store_error",
            ) from None

    def _create_events(self, plan: Plan, created: dict[str, str]) -> None:
        """Make sure each block has an event; `created` is updated in place.

        A block whose recorded event exists is skipped. A recorded event that
        is NOT_FOUND is dropped from `created` before its replacement is
        created, so a failure never reports an event that does not exist.
        """
        for block in plan.blocks:
            event_id = created.get(block.issue_id)
            if event_id is not None:
                if self._calendar.event_exists(event_id):
                    continue
                del created[block.issue_id]
            created[block.issue_id] = self._calendar.create_block(block)
