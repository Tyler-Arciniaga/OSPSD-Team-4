from pydantic import BaseModel


class Issue(BaseModel):
    """An issue as our API presents it to callers.

    Provider details (Trello card fields) never appear here.
    """

    id: str
    title: str
