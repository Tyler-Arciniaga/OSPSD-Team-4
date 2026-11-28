"""What the person sees: steps and errors in user terms, each with a next step.

The error codes and fields are those of product/docs/ERRORS.md. Each message
says what went wrong and what to do next; none decides anything for the
person.
"""

from focus_day_tui.client import ApiError, FocusDayResult, RequestFailure, Step

STEP_LABELS = {
    "validate": "Checked your request",
    "list_issues": "Read your issues",
    "plan": "Planned your day",
    "create_events": "Booked calendar blocks",
    "write_standup_doc": "Wrote standup doc",
    "save_plan": "Saved plan",
}

# The service each step talks to, as the person knows it.
SERVICE_FOR_STEP = {
    "list_issues": "issue tracker",
    "create_events": "calendar",
    "write_standup_doc": "storage service",
    "save_plan": "plan store",
}

# Where to look when a write may already have happened.
CHECK_FOR_STEP = {
    "list_issues": "the tracker",
    "create_events": "your calendar",
    "write_standup_doc": "storage",
    "save_plan": "the plan store",
}

FIX_AND_SUBMIT = "Fix these and submit again."
NOTHING_TO_RETRY = "Nothing to retry."
RETRY_HINT = "Press r to finish booking."


def step_label(name: str) -> str:
    return STEP_LABELS.get(name, name.replace("_", " ").capitalize())


def step_lines(result: FocusDayResult) -> list[str]:
    """One line per step: ✓ done, ✗ failed (with why), – skipped."""
    lines = []
    for step in result.steps:
        lines.append(_step_line(step, result))
    return lines


def _step_line(step: Step, result: FocusDayResult) -> str:
    label = step_label(step.name)
    if step.status == "ok":
        if step.name == "list_issues" and result.plan is not None:
            count = len(result.plan.blocks) + len(result.plan.unscheduled)
            label += f" ({count})"
        return f"✓ {label}"
    if step.status == "failed":
        error = step.error or result.error
        return f"✗ {label}: {error.message}" if error else f"✗ {label}"
    return f"– {label} (skipped)"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _naming(service: str, message: str) -> str:
    """The API's message, prefixed with the service unless it names it."""
    if service.split()[0] in message.lower():
        return _sentence(message)
    return f"The {service} reported a problem: {_sentence(message)}"


def _service(step: str | None) -> str:
    return SERVICE_FOR_STEP.get(step or "", "service")


def error_lines(
    error: ApiError | None,
    *,
    kind: str = "error",
    http_status: int | None = None,
    trace_id: str | None = None,
    api_url: str = "",
    timeout: float = 0,
    unplaced_details: tuple[tuple[str, str], ...] | None = None,
) -> list[str]:
    """The error panel's lines for one failed request.

    `unplaced_details` are the `invalid_input` details that have no form
    field to sit next to; None means all of them.
    """
    if kind == "unreachable":
        return [f"Cannot reach the service at {api_url}. Is it running?"]
    if kind == "timeout":
        return [
            (
                f"The service did not answer within {timeout:g}s. "
                "It may still finish: check before retrying."
            )
        ]
    if kind == "unexpected" or error is None:
        status = "no status" if http_status is None else str(http_status)
        return [f"The service returned an unexpected response (HTTP {status})."]

    service = _service(error.step)
    lines: list[str]
    match error.code:
        case "invalid_input" if error.step in SERVICE_FOR_STEP and not error.details:
            # A provider refused what the service sent: nothing on the form
            # can fix it.
            lines = [
                _sentence(error.message),
                (
                    f"The {service} refused the request. "
                    "Ask the operator to check its configuration."
                ),
            ]
        case "invalid_input":
            details = error.details if unplaced_details is None else unplaced_details
            lines = [_sentence(error.message)] if not details else []
            lines += [f"{name}: {message}" for name, message in details]
            lines.append(FIX_AND_SUBMIT)
        case "not_found":
            if error.step is None:
                check = (
                    f"Check the API URL ({api_url}): it does not look like the "
                    "focus-day service."
                )
            else:
                check = (
                    f"Check that what the {service} needs still exists "
                    "(ask the operator if unsure)."
                )
            lines = [_sentence(error.message), check]
        case "access_denied":
            lines = [
                f"The {service} denied access.",
                "Ask the account owner to check permissions.",
            ]
        case "service_not_configured":
            lines = [
                f"The {service} is not configured.",
                "Ask the operator to re-authorize or configure it.",
            ]
        case "precondition_failed":
            lines = [
                (
                    "Your plan changed since you reviewed it. "
                    "Submit again to see the new proposal."
                )
            ]
        case "provider_error" if error.retryable:
            lines = [_naming(service, error.message), "Try again in a minute."]
        case "provider_error":
            lines = [_naming(service, error.message)]
        case "internal_error":
            trace = trace_id or "none was returned; give the time it happened"
            lines = [
                (
                    "The service hit an unexpected error. "
                    f"Report it with this trace ID: {trace}."
                )
            ]
        case _:
            lines = [f"{error.message} ({error.code})."]

    if error.may_have_applied:
        where = CHECK_FOR_STEP.get(
            error.step or "", "your calendar, tracker or storage"
        )
        lines.append(
            f"This change may already have been applied. Check {where} before retrying."
        )
    return lines


def failure_lines(
    failure: RequestFailure, *, api_url: str, timeout: float
) -> list[str]:
    return error_lines(
        failure.error,
        kind=failure.kind,
        http_status=failure.http_status,
        trace_id=failure.trace_id,
        api_url=api_url,
        timeout=timeout,
    )
