"""An optional tracing hook, so the package carries no tracing library.

Every outbound Trello request runs inside `provider_span("trello.request")`.
By default that does nothing. A service that traces its requests can pass its
own span factory to `set_span_factory()`, for example a function wrapping
OpenTelemetry's `tracer.start_as_current_span`. The span object is never used
by this package, so the factory may yield anything.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

SpanFactory = Callable[[str], AbstractContextManager[object]]


@contextmanager
def _no_span(name: str) -> Iterator[None]:
    yield None


_span_factory: SpanFactory = _no_span


def set_span_factory(factory: SpanFactory | None) -> None:
    """Trace provider requests with `factory`, or stop tracing with None."""
    global _span_factory
    _span_factory = factory if factory is not None else _no_span


def provider_span(name: str) -> AbstractContextManager[object]:
    """Return a context manager spanning one outbound provider request."""
    return _span_factory(name)
