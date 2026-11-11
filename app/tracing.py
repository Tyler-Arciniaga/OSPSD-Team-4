from time import perf_counter

from opentelemetry.trace import SpanKind, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.telemetry import OPERATIONS, use_request_tracer


class RequestTracingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        telemetry = getattr(scope["app"].state, "telemetry", None)
        if telemetry is None:
            await self.app(scope, receive, send)
            return

        tracer = telemetry.tracer
        status = 500
        started = perf_counter()

        with (
            use_request_tracer(tracer),
            tracer.start_as_current_span(
                "request",
                kind=SpanKind.SERVER,
                record_exception=False,
                set_status_on_exception=False,
            ) as span,
        ):
            span.set_attribute("outcome", "success")

            async def traced_send(message: Message) -> None:
                nonlocal status

                if message["type"] == "http.response.start":
                    status = message["status"]

                    trace_id = f"{span.get_span_context().trace_id:032x}"
                    headers = list(message.get("headers", []))
                    headers.append((b"x-trace-id", trace_id.encode("ascii")))
                    message["headers"] = headers

                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            except Exception:
                span.set_attribute("outcome", "internal_error")
                span.set_status(StatusCode.ERROR)
                raise
            else:
                span.set_status(StatusCode.ERROR if status >= 400 else StatusCode.OK)
            finally:
                endpoint = scope.get("endpoint")
                operation = getattr(endpoint, "__name__", "routing")

                if status == 405 or operation not in OPERATIONS:
                    operation = "routing"

                attributes = getattr(span, "attributes", None) or {}
                outcome = str(attributes.get("outcome", "success"))

                if status >= 400 and outcome == "success":
                    outcome = "internal_error"

                span.update_name(operation)
                span.set_attribute("operation", operation)
                span.set_attribute("outcome", outcome)
                span.set_attribute("http.response.status_code", status)

                telemetry.record_request(
                    operation=operation,
                    status=status,
                    duration_seconds=perf_counter() - started,
                    outcome=outcome,
                )
