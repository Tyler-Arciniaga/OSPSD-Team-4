from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# The format of a full issue ID, shared by request validation and
# provider response checks.
ISSUE_ID_PATTERN = r"[0-9a-f]{24}"


class Issue(BaseModel):
    """An issue as our API presents it to callers.

    Provider details (Trello card fields) never appear here.
    """

    id: str
    title: str


class IssueCreate(BaseModel):
    """The fields a caller supplies to create an issue.

    The title limit is a service rule, not a provider limit.
    """

    model_config = ConfigDict(extra="forbid")

    # enforce title to be non-empty string.
    title: Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
