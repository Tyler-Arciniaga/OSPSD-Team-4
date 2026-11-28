"""Launch options: checked before the UI opens (issue #88)."""

import subprocess
import sys

import pytest

from focus_day_tui.cli import parse_args


def test_defaults() -> None:
    args = parse_args([], environ={})
    assert args.api_url == "http://localhost:8000"
    assert args.timeout == 60


def test_the_environment_sets_the_api_url() -> None:
    args = parse_args([], environ={"FOCUS_DAY_API_URL": "https://focus.example/"})
    assert args.api_url == "https://focus.example"


def test_options_override_the_environment() -> None:
    args = parse_args(
        ["--api-url", "http://localhost:9000", "--timeout", "2.5"],
        environ={"FOCUS_DAY_API_URL": "https://focus.example"},
    )
    assert (args.api_url, args.timeout) == ("http://localhost:9000", 2.5)


@pytest.mark.parametrize(
    ("argv", "environ", "problem"),
    [
        (["--timeout", "soon"], {}, "'soon' is not a number"),
        (["--timeout", "0"], {}, "positive number of seconds"),
        (["--timeout", "nan"], {}, "positive number of seconds"),
        (["--api-url", "localhost:8000"], {}, "not an http:// or https:// URL"),
        ([], {"FOCUS_DAY_API_URL": "ftp://x"}, "FOCUS_DAY_API_URL"),
        (["--verbose"], {}, "unrecognized arguments"),
    ],
)
def test_bad_launch_options_exit_with_code_2_and_usage(
    argv: list[str],
    environ: dict[str, str],
    problem: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv, environ=environ)
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: focus-day")
    assert problem in err


def test_the_console_script_exits_before_the_ui_opens() -> None:
    """`focus-day --timeout soon` as a person would type it."""
    result = subprocess.run(
        [sys.executable, "-c", "from focus_day_tui.cli import main; main()"]
        + ["--timeout", "soon"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "usage: focus-day" in result.stderr
    assert result.stdout == ""
