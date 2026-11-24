"""`focus-day [--api-url URL] [--timeout SECONDS]`: open the TUI.

Launch options are checked before the UI opens; a bad one exits with code 2
and a usage message (argparse's behavior). An unreachable API does not stop
the app: it opens and shows the connection problem.
"""

import argparse
import os
from collections.abc import Mapping, Sequence

from focus_day_tui.app import DEFAULT_API_URL, DEFAULT_TIMEOUT, FocusDayApp
from focus_day_tui.state import default_state_dir

API_URL_ENV = "FOCUS_DAY_API_URL"


def _url(value: str) -> str:
    if not value.startswith(("http://", "https://")) or len(value) <= len("https://"):
        raise argparse.ArgumentTypeError(f"{value!r} is not an http:// or https:// URL")
    return value.rstrip("/")


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 0 < seconds < float("inf"):
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return seconds


def parse_args(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="focus-day",
        description="Plan and book a focus day through the product API.",
    )
    parser.add_argument(
        "--api-url",
        type=_url,
        default=None,
        help=f"the product API (env {API_URL_ENV}, default {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=_seconds,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to wait for each request (default {DEFAULT_TIMEOUT:g})",
    )
    args = parser.parse_args(argv)
    if args.api_url is None:
        try:
            args.api_url = _url(environ.get(API_URL_ENV) or DEFAULT_API_URL)
        except argparse.ArgumentTypeError as exc:
            parser.error(f"{API_URL_ENV}: {exc}")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    FocusDayApp(
        api_url=args.api_url, timeout=args.timeout, state_dir=default_state_dir()
    ).run()
