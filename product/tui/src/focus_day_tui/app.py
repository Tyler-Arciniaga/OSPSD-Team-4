"""The Textual app: the Focus day screen.

Every HTTP call runs in a Textual worker, so the screen stays responsive
(`Esc` stops waiting, `q` quits) while the API works. The screen converts
the form into the API's request shape and shows what comes back; it never
decides what to plan, book or retry.

More screens (Progress, Status) are added next to it when their endpoints land.
"""

from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, LoadingIndicator, Static

from focus_day_tui import timeline
from focus_day_tui.client import ApiClient, FocusDayResult, RequestFailure, plan_or_none
from focus_day_tui.form import (
    BusyRow,
    FormValues,
    build_request,
    form_key_for_api_field,
    parse_clock,
    parse_day,
    parse_zone,
)
from focus_day_tui.messages import (
    FIX_AND_SUBMIT,
    NOTHING_TO_RETRY,
    RETRY_HINT,
    error_lines,
    failure_lines,
    step_lines,
)
from focus_day_tui.state import LastRun, LocalStateStore, Preferences
from focus_day_tui.zones import system_timezone

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 60.0

WAITING_SUBMIT = "Planning and booking your focus blocks…"
WAITING_RETRY = "Finishing your bookings…"
STOPPED_WAITING = (
    "Stopped waiting. The service may still finish: "
    "check your calendar before submitting again."
)


class BusyRowWidget(Vertical):
    """One busy interval: start and end as HH:MM, and a remove button."""

    def __init__(self, start: str = "", end: str = "") -> None:
        super().__init__(classes="busy-row")
        self._start, self._end = start, end

    def compose(self) -> ComposeResult:
        with Horizontal(classes="line"):
            yield Input(
                self._start, placeholder="HH:MM", classes="busy-start", compact=True
            )
            yield Static("–", classes="dash")
            yield Input(
                self._end, placeholder="HH:MM", classes="busy-end", compact=True
            )
            yield Button("remove", classes="remove-busy", compact=True)
        yield Static("", classes="field-error")

    @property
    def value(self) -> BusyRow:
        return BusyRow(
            start=self.query_one(".busy-start", Input).value,
            end=self.query_one(".busy-end", Input).value,
        )


class FocusDayScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "retry", "Retry"),
        # Reached only when Retry is unavailable (check_action hides it).
        Binding("r", "nothing_to_retry", show=False),
        Binding("c", "copy_doc", "Copy doc path"),
        Binding("escape", "stop_waiting", "Stop waiting"),
        Binding("escape", "leave_field", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.loading_text: str | None = None
        self.last_run: LastRun | None = None
        self.doc_path: str | None = None
        self.requested = False
        # Blocks of the last result, and the day/zone they were drawn for.
        self.result_items: list[timeline.Item] = []
        self.result_day: str | None = None

    @property
    def focus_app(self) -> FocusDayApp:
        return cast(FocusDayApp, self.app)

    # Layout

    def compose(self) -> ComposeResult:
        app = self.focus_app
        saved = app.store.load_preferences()
        yield Static("[b reverse] f [/] Focus day", id="tabs")
        with Horizontal(id="main"):
            with VerticalScroll(id="form"):
                yield from self._field("Assignee", "assignee", saved.assignee)
                yield from self._field(
                    "Day", "day", app.today.isoformat(), placeholder="YYYY-MM-DD"
                )
                with Horizontal(classes="line"):
                    yield Label("Window", classes="field-label")
                    yield Input(
                        saved.window_start,
                        placeholder="HH:MM",
                        id="window-start",
                        compact=True,
                    )
                    yield Static("–", classes="dash")
                    yield Input(
                        saved.window_end,
                        placeholder="HH:MM",
                        id="window-end",
                        compact=True,
                    )
                yield Static("", id="error-window", classes="field-error")
                yield from self._field(
                    "Block min",
                    "block_minutes",
                    saved.block_minutes,
                    placeholder="service default",
                )
                yield from self._field(
                    "Time zone", "timezone", saved.timezone or app.system_zone
                )
                yield Label("Busy", classes="section")
                yield Vertical(id="busy-rows")
                yield Button("+ add busy time", id="add-busy", compact=True)
                yield Button("Submit", id="submit", variant="primary", compact=True)
            with VerticalScroll(id="right"):
                yield Static("", id="timeline")
                yield Static("", id="unscheduled")
        with VerticalScroll(id="results"):
            with Horizontal(id="loading"):
                yield LoadingIndicator()
                yield Label("", id="loading-text")
            yield Static("", id="steps")
            yield Static("", id="messages")
            yield Static("", id="doc")
        yield Footer()

    def _field(
        self, label: str, key: str, value: str, placeholder: str = ""
    ) -> ComposeResult:
        with Horizontal(classes="line"):
            yield Label(label, classes="field-label")
            yield Input(
                value, placeholder=placeholder, id=key.replace("_", "-"), compact=True
            )
        yield Static("", id=f"error-{key.replace('_', '-')}", classes="field-error")

    def on_mount(self) -> None:
        self.last_run = self.focus_app.store.load_last_run()
        self.query_one("#loading").display = False
        self.refresh_timeline()
        self.check_service()

    def _get(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value

    # Form

    def form_values(self) -> FormValues:
        return FormValues(
            assignee=self._get("assignee"),
            day=self._get("day"),
            window_start=self._get("window-start"),
            window_end=self._get("window-end"),
            block_minutes=self._get("block-minutes"),
            timezone=self._get("timezone"),
            busy=tuple(row.value for row in self.query(BusyRowWidget)),
        )

    def show_field_errors(self, errors: dict[str, str]) -> None:
        for widget in self.query("#form .field-error"):
            cast(Static, widget).update("")
        rows = list(self.query(BusyRowWidget))
        for key, message in errors.items():
            if key.startswith("busy["):
                index = int(key[5:-1])
                if index < len(rows):
                    rows[index].query_one(".field-error", Static).update(
                        escape(message)
                    )
                continue
            self.query_one(f"#error-{key.replace('_', '-')}", Static).update(
                escape(message)
            )

    def show_messages(self, lines: list[str], kind: str = "info") -> None:
        panel = self.query_one("#messages", Static)
        panel.set_classes(kind)
        panel.update(escape("\n".join(lines)))

    @on(Input.Changed)
    def _form_changed(self) -> None:
        self.refresh_timeline()

    @on(Button.Pressed, "#add-busy")
    async def add_busy_row(self) -> None:
        row = BusyRowWidget()
        await self.query_one("#busy-rows").mount(row)
        row.query_one(".busy-start", Input).focus()

    @on(Button.Pressed, ".remove-busy")
    async def remove_busy_row(self, event: Button.Pressed) -> None:
        row = event.button.query_ancestor(BusyRowWidget)
        await row.remove()
        self.refresh_timeline()

    @on(Button.Pressed, "#submit")
    @on(Input.Submitted)
    def submit(self) -> None:
        if self.loading_text is not None:
            return
        values = self.form_values()
        body, errors = build_request(values)
        self.show_field_errors(errors)
        if body is None:
            self.show_messages(["Some fields need fixing.", FIX_AND_SUBMIT], "error")
            return
        self.run_focus_day(body, retry=False, values=values)

    # Timeline

    def refresh_timeline(self) -> None:
        """Draw what the form says now, plus the last result's blocks."""
        values = self.form_values()
        day = parse_day(values.day)
        zone_name = values.timezone.strip() or "UTC"
        start = parse_clock(values.window_start)
        end = parse_clock(values.window_end)
        window = (start, end) if start and end and start < end else None
        busy: list[tuple[time, time]] = []
        for row in values.busy:
            begin, finish = parse_clock(row.start), parse_clock(row.end)
            if begin and finish and begin < finish:
                busy.append((begin, finish))
        items = timeline.busy_items(busy)
        if day is not None and day.isoformat() == self.result_day:
            items += self.result_items
        self.query_one("#timeline", Static).update(
            timeline.render(day, zone_name, window, items)
        )

    # Requests

    def show_waiting(self, text: str | None) -> None:
        self.loading_text = text
        self.query_one("#loading").display = text is not None
        self.query_one("#loading-text", Label).update(text or "")
        self.query_one("#form").disabled = text is not None
        if text is not None:
            self.set_focus(None)
        self.refresh_bindings()

    @work(exclusive=True, group="ready")
    async def check_service(self) -> None:
        app = self.focus_app
        self.show_messages([f"Checking the service at {app.client.base_url}…"])
        failure = await app.client.ready()
        if self.requested:
            return  # the person already submitted; keep that result on screen
        if failure is not None:
            self.show_messages(
                failure_lines(
                    failure, api_url=app.client.base_url, timeout=app.client.timeout
                ),
                "error",
            )
        elif self.last_run is not None:
            request = self.last_run.request
            self.show_messages(
                [
                    (
                        f"The focus day for {request.get('assignee', '?')} on "
                        f"{request.get('day', '?')} stopped partway."
                    ),
                    RETRY_HINT,
                ]
            )
        else:
            self.show_messages(
                [
                    (
                        f"Connected to {app.client.base_url}. "
                        "Fill in the form and press Submit."
                    )
                ]
            )

    @work(exclusive=True, group="request")
    async def run_focus_day(
        self, body: dict[str, Any], *, retry: bool, values: FormValues | None
    ) -> None:
        self.requested = True
        self.show_waiting(WAITING_RETRY if retry else WAITING_SUBMIT)
        try:
            outcome = await self.focus_app.client.focus_day(body)
        finally:
            self.show_waiting(None)
        if isinstance(outcome, RequestFailure):
            self.show_failure(outcome, values)
        else:
            self.show_result(outcome, body, retry=retry, values=values)

    def show_failure(self, failure: RequestFailure, values: FormValues | None) -> None:
        client = self.focus_app.client
        if failure.error and failure.error.code == "invalid_input":
            unplaced = self._place_details(failure.error.details, values)
            lines = error_lines(
                failure.error,
                http_status=failure.http_status,
                unplaced_details=unplaced,
            )
        else:
            lines = failure_lines(
                failure, api_url=client.base_url, timeout=client.timeout
            )
        self.show_messages(lines, "error")

    def _place_details(
        self, details: tuple[tuple[str, str], ...], values: FormValues | None
    ) -> tuple[tuple[str, str], ...]:
        """Put each 422 detail next to its field; return those with no field."""
        placed: dict[str, str] = {}
        unplaced: list[tuple[str, str]] = []
        for name, message in details:
            key = form_key_for_api_field(name, values) if values else None
            if key is None:
                unplaced.append((name, message))
            else:
                placed[key] = f"{placed[key]}; {message}" if key in placed else message
        self.show_field_errors(placed)
        return tuple(unplaced)

    def show_result(
        self,
        result: FocusDayResult,
        body: dict[str, Any],
        *,
        retry: bool,
        values: FormValues | None,
    ) -> None:
        app = self.focus_app
        request = {key: value for key, value in body.items() if key != "replay"}
        replay = body.get("replay")
        if result.plan is None and isinstance(replay, dict):
            # A replay answers for the plan it was sent.
            result = replace(result, plan=plan_or_none(replay.get("plan")))
        self.query_one("#steps", Static).update(escape("\n".join(step_lines(result))))

        day = parse_day(str(request.get("day", "")))
        zone = parse_zone(str(request.get("timezone", ""))) or ZoneInfo("UTC")
        self.result_day = day.isoformat() if day else None
        self.result_items = timeline.block_items(result, day, zone) if day else []
        self.refresh_timeline()
        unscheduled = result.plan.unscheduled if result.plan else ()
        self.query_one("#unscheduled", Static).update(
            "Not scheduled (no free slot): " + escape(", ".join(unscheduled))
            if unscheduled
            else ""
        )
        self.doc_path = result.doc_path
        self.query_one("#doc", Static).update(
            f"Standup doc: [b]{escape(result.doc_path)}[/b]  (press c to copy)"
            if result.doc_path
            else ""
        )

        if result.error is None:
            lines = ["Your focus day is booked."]
            if unscheduled:
                lines.append(f"{len(unscheduled)} issue(s) did not fit; see the list.")
            try:
                app.store.save_preferences(Preferences.from_request(request))
                if retry:
                    app.store.clear_last_run()
            except OSError:
                lines.append(f"Could not save to {app.store.directory}.")
            if retry:
                self.last_run = None
            self.show_messages(lines, "ok")
        else:
            unplaced = None
            if result.error.code == "invalid_input":
                unplaced = self._place_details(result.error.details, values)
            lines = error_lines(
                result.error,
                http_status=result.http_status,
                trace_id=result.trace_id,
                api_url=app.client.base_url,
                timeout=app.client.timeout,
                unplaced_details=unplaced,
            )
            if result.plan is not None and result.failed_step in (
                "create_events",
                "write_standup_doc",
            ):
                run = LastRun(
                    request=request, plan=result.plan.raw, created=result.created
                )
                try:
                    app.store.save_last_run(run)
                    self.last_run = run
                    lines.append(RETRY_HINT)
                except OSError:
                    lines.append(
                        f"Could not save the run to {app.store.directory}, "
                        "so it cannot be retried from here."
                    )
            self.show_messages(lines, "error")
        self.refresh_bindings()

    # Actions

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        waiting = self.loading_text is not None
        if action == "retry":
            return self.last_run is not None and not waiting
        if action == "copy_doc":
            return self.doc_path is not None
        if action == "stop_waiting":
            return waiting
        if action == "leave_field":
            return not waiting
        return True

    def action_retry(self) -> None:
        if self.last_run is not None:
            self.run_focus_day(self.last_run.replay_request(), retry=True, values=None)

    def action_nothing_to_retry(self) -> None:
        if self.loading_text is None:
            self.show_messages([NOTHING_TO_RETRY])

    def action_copy_doc(self) -> None:
        if self.doc_path:
            self.app.copy_to_clipboard(self.doc_path)
            self.notify(f"Copied {self.doc_path}")

    def action_stop_waiting(self) -> None:
        self.workers.cancel_group(self, "request")
        self.show_waiting(None)
        self.show_messages([STOPPED_WAITING], "error")

    def action_leave_field(self) -> None:
        """Esc leaves a text field, so the single-key shortcuts work."""
        self.set_focus(None)


class FocusDayApp(App[None]):
    TITLE = "focus-day"
    CSS_PATH = "app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f", "focus_day", "Focus day"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = DEFAULT_TIMEOUT,
        state_dir: Path,
        transport: httpx.AsyncBaseTransport | None = None,
        today: date | None = None,
        system_zone: str | None = None,
    ) -> None:
        super().__init__()
        self.client = ApiClient(api_url, timeout, transport)
        self.store = LocalStateStore(state_dir)
        self.today = today or date.today()
        self.system_zone = system_zone or system_timezone()

    def get_default_screen(self) -> Screen[Any]:
        # The default screen, not an app mode: with MODES, a screen whose
        # Inputs start with a value fails in a real terminal (Textual 8.2.8,
        # ScreenStackError from Input._watch_selection while the mode's stack
        # is empty). Progress and Status can be added as modes or pushed
        # screens when they land.
        return FocusDayScreen()

    def action_focus_day(self) -> None:
        self.screen.query_one("#assignee", Input).focus()
