"""Request and response bodies of `POST /v1/focus-day`.

The error body extends the HW1 shape (`issue_tracker_contract.errors`):
`error.code` and `error.message` keep their HW1 meaning, and the product adds
the failed step and whether retrying is useful or a write may have taken
effect. See product/docs/ERRORS.md.
"""

from datetime import date
from typing import Annotated, Literal

from issue_tracker_contract.errors import ErrorBody
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ospsd_team4_product.config import MAX_MINUTES
from ospsd_team4_product.errors import Step
from ospsd_team4_product.planner import Block, Plan, Unscheduled

# Wall-clock time of day, 00:00 to 23:59.
ClockTime = Annotated[
    str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
]
IsoDay = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkingWindowModel(_Strict):
    start: ClockTime
    end: ClockTime


class BusyModel(_Strict):
    """Caller-supplied busy time. The planner checks the timestamp format."""

    start: str
    end: str


class BlockModel(_Strict):
    issue_id: str
    title: str
    start: str
    end: str

    @classmethod
    def of(cls, block: Block) -> BlockModel:
        return cls(
            issue_id=block.issue_id, title=block.title, start=block.start, end=block.end
        )

    def to_block(self) -> Block:
        return Block(
            issue_id=self.issue_id, title=self.title, start=self.start, end=self.end
        )


class UnscheduledModel(_Strict):
    issue_id: str
    reason: Literal["no_free_slot"]


class PlanModel(_Strict):
    blocks: list[BlockModel]
    unscheduled: list[UnscheduledModel]

    @classmethod
    def of(cls, plan: Plan) -> PlanModel:
        return cls(
            blocks=[BlockModel.of(block) for block in plan.blocks],
            unscheduled=[
                UnscheduledModel(issue_id=item.issue_id, reason=item.reason)
                for item in plan.unscheduled
            ],
        )

    def to_plan(self) -> Plan:
        return Plan(
            blocks=tuple(block.to_block() for block in self.blocks),
            unscheduled=tuple(
                Unscheduled(issue_id=item.issue_id, reason=item.reason)
                for item in self.unscheduled
            ),
        )


class ReplayModel(_Strict):
    """The `plan` and `created` of an earlier response, sent back to retry it."""

    plan: PlanModel
    created: dict[str, str] = Field(default_factory=dict)


class FocusDayRequest(_Strict):
    assignee: str
    day: IsoDay
    timezone: str | None = None
    working_window: WorkingWindowModel | None = None
    busy: list[BusyModel] = Field(default_factory=list)
    default_minutes: int | None = Field(default=None, ge=1, le=MAX_MINUTES)
    replay: ReplayModel | None = None

    @field_validator("day")
    @classmethod
    def _real_day(cls, value: str) -> str:
        date.fromisoformat(value)  # rejects e.g. 2026-02-30
        return value

    def parsed_day(self) -> date:
        return date.fromisoformat(self.day)


StepStatus = Literal["ok", "failed", "skipped"]


class FocusDayErrorBody(ErrorBody):
    step: Step
    retryable: bool
    may_have_applied: bool


class StepModel(BaseModel):
    name: Step
    status: StepStatus
    error: FocusDayErrorBody | None = None


class DocModel(BaseModel):
    path: str
    id: str | None


class FocusDayResponse(BaseModel):
    """200: every step finished. Unscheduled issues are not a failure."""

    status: Literal["ok"]
    steps: list[StepModel]
    plan: PlanModel
    created: dict[str, str]
    doc: DocModel
    issues_truncated: bool | None
    failed_step: None = None


class FocusDayErrorResponse(BaseModel):
    """A step failed. `plan` and `created` are what a retry sends back."""

    status: Literal["partial", "failed"]
    steps: list[StepModel]
    plan: PlanModel | None
    created: dict[str, str]
    doc: None = None
    issues_truncated: bool | None
    failed_step: Step
    error: FocusDayErrorBody
