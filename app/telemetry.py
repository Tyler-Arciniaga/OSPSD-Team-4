from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.trace import Tracer

from app.health import deployed_commit


def create_tracer_provider() -> TracerProvider:
    resource = Resource.create(
        {
            "service.name": "ospsd-team4-issue-tracker",
            "service.version": deployed_commit(),
        }
    )

    provider = TracerProvider(resource=resource)

    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    return provider


_request_tracer: ContextVar[Tracer | None] = ContextVar(
    "request_tracer",
    default=None,
)


@contextmanager
def use_request_tracer(tracer: Tracer) -> Iterator[None]:
    token = _request_tracer.set(tracer)
    try:
        yield
    finally:
        _request_tracer.reset(token)
