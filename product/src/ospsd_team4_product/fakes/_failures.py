"""Failure injection shared by the fakes."""

from collections import Counter


class FailureSchedule:
    """Raise a chosen exception on the n-th call of an operation.

    `always` makes every call of every operation raise it instead.
    """

    def __init__(self, always: Exception | None = None) -> None:
        self.always = always
        self._calls: Counter[str] = Counter()
        self._planned: dict[tuple[str, int], Exception] = {}

    def fail(self, operation: str, error: Exception, *, on_call: int = 1) -> None:
        """Raise `error` on call number `on_call` (1-based) of `operation`."""
        self._planned[(operation, self._calls[operation] + on_call)] = error

    def check(self, operation: str) -> None:
        self._calls[operation] += 1
        if self.always is not None:
            raise self.always
        error = self._planned.pop((operation, self._calls[operation]), None)
        if error is not None:
            raise error

    def calls(self, operation: str) -> int:
        return self._calls[operation]
