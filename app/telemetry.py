import json
import logging
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import (
    INVALID_SPAN,
    Span,
    SpanKind,
    StatusCode,
    Tracer,
    get_current_span,
)

from app.health import deployed_commit
from issue_tracker_contract.errors import ErrorCode

logger = logging.getLogger("app.telemetry")

OPERATIONS = frozenset(
    {
        "get_issue",
        "create_issue",
        "update_issue",
        "delete_issue",
        "healthz",
        "readyz",
        "routing",
    }
)

OUTCOMES = frozenset({"success"} | {code.value for code in ErrorCode})

_request_tracer: ContextVar[Tracer | None] = ContextVar(
    "request_tracer",
    default=None,
)


class _SafeExporterLogFilter(logging.Filter):
    """Prevent exporter diagnostics from printing backend response data."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "Telemetry export diagnostic; check the observability connection"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


_exporter_log_filter = _SafeExporterLogFilter()


def _remote_export_enabled() -> bool:
    """Remote export is opt-in; otherwise telemetry goes to the console."""
    return os.getenv("OTEL_ENABLED", "false").lower() == "true"


def _validate_export_config() -> None:
    if not _remote_export_enabled():
        return

    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT is required when OTEL_ENABLED=true"
        )

    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    if protocol != "http/protobuf":
        raise ValueError("OTEL_EXPORTER_OTLP_PROTOCOL must be http/protobuf")

    # These diagnostics can otherwise include endpoint or response details.
    for name in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        "opentelemetry.exporter.otlp.proto.http._log_exporter",
        "opentelemetry.sdk._shared_internal",
    ):
        logging.getLogger(name).addFilter(_exporter_log_filter)


def _create_resource() -> Resource:
    service_name = (
        os.getenv("OTEL_SERVICE_NAME")
        or os.getenv("RENDER_SERVICE_NAME")
        or "ospsd-team4-issue-tracker"
    )

    return Resource.create(
        {
            "service.name": service_name,
            "service.version": deployed_commit(),
        }
    )


def create_tracer_provider(
    resource: Resource | None = None,
) -> TracerProvider:
    """Configure tracing with bounded background export."""
    _validate_export_config()

    if resource is None:
        resource = _create_resource()

    provider = TracerProvider(
        resource=resource,
        sampler=ALWAYS_ON,
        shutdown_on_exit=False,
    )

    exporter = (
        OTLPSpanExporter(timeout=3)
        if _remote_export_enabled()
        else ConsoleSpanExporter()
    )

    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=256,
            max_export_batch_size=64,
            schedule_delay_millis=1000,
        )
    )

    return provider


def create_meter_provider(
    resource: Resource | None = None,
) -> MeterProvider:
    """Configure periodic metric export."""
    _validate_export_config()

    if resource is None:
        resource = _create_resource()

    exporter = (
        OTLPMetricExporter(timeout=3)
        if _remote_export_enabled()
        else ConsoleMetricExporter()
    )

    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=10000,
        export_timeout_millis=5000,
    )

    return MeterProvider(
        resource=resource,
        metric_readers=[reader],
        shutdown_on_exit=False,
    )


def create_logger_provider(
    resource: Resource | None = None,
) -> LoggerProvider:
    """Export only explicitly emitted application outcome logs."""
    _validate_export_config()

    if resource is None:
        resource = _create_resource()

    provider = LoggerProvider(
        resource=resource,
        shutdown_on_exit=False,
    )

    exporter = (
        OTLPLogExporter(timeout=3)
        if _remote_export_enabled()
        else ConsoleLogRecordExporter()
    )

    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            exporter,
            max_queue_size=256,
            max_export_batch_size=64,
            schedule_delay_millis=1000,
        )
    )

    return provider


@contextmanager
def use_request_tracer(tracer: Tracer) -> Iterator[None]:
    """Make the tracer available during this request's execution."""
    token = _request_tracer.set(tracer)
    try:
        yield
    finally:
        _request_tracer.reset(token)


@contextmanager
def provider_span(name: str) -> Iterator[Span]:
    """Trace an outbound provider call without capturing sensitive values."""
    tracer = _request_tracer.get()

    if tracer is None:
        yield INVALID_SPAN
        return

    with tracer.start_as_current_span(
        name,
        kind=SpanKind.CLIENT,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception:
            span.set_status(StatusCode.ERROR)
            raise


class Telemetry:
    """Own the application's tracing, metrics, and outcome logging."""

    def __init__(self) -> None:
        _validate_export_config()

        try:
            memory_limit_mb = float(os.getenv("OTEL_MEMORY_LIMIT_MB", "512"))
        except ValueError:
            raise ValueError(
                "OTEL_MEMORY_LIMIT_MB must be finite and positive"
            ) from None

        if not math.isfinite(memory_limit_mb) or memory_limit_mb <= 0:
            raise ValueError("OTEL_MEMORY_LIMIT_MB must be finite and positive")

        self.memory_limit_bytes = memory_limit_mb * 1024 * 1024
        self.release = deployed_commit()
        self.resource = _create_resource()
        self._closed = False

        # Share one resource so all three signals identify the same instance.
        self.tracer_provider = create_tracer_provider(self.resource)
        self.meter_provider = create_meter_provider(self.resource)
        self.logger_provider = create_logger_provider(self.resource)

        self.tracer = self.tracer_provider.get_tracer("issue-service")
        self.outcome_logger = self.logger_provider.get_logger("issue-service")

        meter = self.meter_provider.get_meter("issue-service")

        self.requests = meter.create_counter(
            "issue_requests",
            unit="1",
            description="Completed HTTP requests by operation and outcome",
        )

        self.errors = meter.create_counter(
            "issue_errors",
            unit="1",
            description="Failed HTTP requests by operation and shared error category",
        )

        self.duration = meter.create_histogram(
            "issue_request_duration",
            unit="s",
            description="HTTP request duration by operation and outcome",
            explicit_bucket_boundaries_advisory=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                30,
            ),
        )

        meter.create_observable_gauge(
            "issue_memory_saturation",
            unit="1",
            description="Process RSS divided by the configured instance memory limit",
            callbacks=[self._observe_memory],
        )

    def _observe_memory(self, _options: object) -> list[Observation]:
        """Measure current process memory on Linux, including idle periods."""
        try:
            with open("/proc/self/statm") as stream:
                resident_pages = int(stream.read().split()[1])

            resident_bytes = resident_pages * os.sysconf("SC_PAGE_SIZE")

            return [
                Observation(
                    resident_bytes / self.memory_limit_bytes,
                    {"release": self.release},
                )
            ]
        except (OSError, ValueError, IndexError):
            # No sample on unsupported platforms, such as the local Mac.
            # Missing data must not be reported as zero memory usage.
            return []

    def record_request(
        self,
        operation: str,
        status: int,
        duration_seconds: float,
        outcome: str,
    ) -> None:
        """Record one completed request without bodies, URLs, or resource IDs."""
        try:
            if operation not in OPERATIONS:
                operation = "routing"

            if outcome not in OUTCOMES:
                outcome = "internal_error"

            if status >= 400 and outcome == "success":
                outcome = "internal_error"

            attributes = {
                "operation": operation,
                "outcome": outcome,
                "release": self.release,
            }

            self.requests.add(1, attributes)
            self.duration.record(duration_seconds, attributes)

            if outcome != "success":
                self.errors.add(1, attributes)

            span_context = get_current_span().get_span_context()

            record = {
                **attributes,
                "status": status,
                "duration_ms": round(duration_seconds * 1000, 3),
                "trace_id": f"{span_context.trace_id:032x}",
                "span_id": f"{span_context.span_id:016x}",
            }

            if status >= 500:
                severity = SeverityNumber.ERROR
            elif status >= 400:
                severity = SeverityNumber.WARN
            else:
                severity = SeverityNumber.INFO

            self.outcome_logger.emit(
                body=json.dumps(record),
                severity_number=severity,
                attributes=attributes,
            )
        except Exception:  # noqa: BLE001
            # A measurement failure must not fail or retry the API request.
            logger.warning("Request telemetry could not be recorded")

    def shutdown(self) -> None:
        """Drain exporters and stop their workers once."""
        if self._closed:
            return

        self._closed = True

        for provider in (
            self.meter_provider,
            self.logger_provider,
            self.tracer_provider,
        ):
            try:
                provider.shutdown()
            except Exception:  # noqa: BLE001
                logger.warning("A telemetry component could not shut down cleanly")
