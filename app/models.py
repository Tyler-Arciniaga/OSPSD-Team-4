from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The format of a full issue ID, shared by request validation and
# provider response checks.
ISSUE_ID_PATTERN = r"[0-9a-f]{24}"

# The longest title a caller may set. This is a service rule, not a
# provider limit.
MAX_TITLE_LENGTH = 256

# A caller-supplied title: non-empty, at most MAX_TITLE_LENGTH characters,
# and containing at least one non-whitespace character. Shared by every
# request model that accepts a title, so create and update follow one rule.
IssueTitle = Annotated[
    str, Field(min_length=1, max_length=MAX_TITLE_LENGTH, pattern=r"\S")
]


class Issue(BaseModel):
    """An issue as our API presents it to callers.

    Provider details (Trello card fields) never appear here.
    """

    id: str
    title: str


class IssueCreate(BaseModel):
    """The fields a caller supplies to create an issue."""

    model_config = ConfigDict(extra="forbid")

    title: IssueTitle


class IssueUpdate(BaseModel):
    """Fields a caller may change on an issue.

    Omitted fields are left unchanged. At least one field must be supplied.
    """

    model_config = ConfigDict(extra="forbid")

    title: IssueTitle | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> "IssueUpdate":
        if not self.model_fields_set:
            raise ValueError("Request body must include at least one field")
        if self.title is None:
            raise ValueError("title cannot be null")
        return self
