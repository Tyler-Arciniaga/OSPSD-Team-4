"""The real app in a real (pseudo-)terminal, as `focus-day` starts it.

`App.run_test()` is headless and missed a startup crash that only happened
in a terminal (see FocusDayApp.get_default_screen), so this test starts the
app the way a person does, with an unreachable API: it must open, show the
connection message, and quit with `q`.
"""

import os
import re
import select
import sys
import time
from pathlib import Path

import pytest

pty = pytest.importorskip("pty")

UNREACHABLE = "http://127.0.0.1:9"


def _read(fd: int, seconds: float) -> bytes:
    ready, _, _ = select.select([fd], [], [], seconds)
    if not ready:
        return b""
    try:
        return os.read(fd, 65536)
    except OSError:
        return b""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX terminal")
def test_the_app_opens_in_a_terminal_when_the_api_is_unreachable(
    tmp_path: Path,
) -> None:
    pid, fd = pty.fork()
    if pid == 0:  # the child: run the console script's entry point
        os.environ["TERM"] = "xterm-256color"
        os.environ["FOCUS_DAY_STATE_DIR"] = str(tmp_path)
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-c",
                "from focus_day_tui.cli import main; main()",
                "--api-url",
                UNREACHABLE,
            ],
        )

    output = b""
    expected = f"Cannot reach the service at {UNREACHABLE}. Is it running?"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        output += _read(fd, 0.2)
        shown = re.sub(
            r"\x1b\[[0-9;?<>=]*[A-Za-z~]", "", output.decode(errors="replace")
        )
        if expected in shown or "Traceback" in shown or "Error" in shown:
            break
    assert "Traceback" not in shown and "Error" not in shown, shown[-2000:]
    assert expected in shown

    os.write(fd, b"q")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            assert os.waitstatus_to_exitcode(status) == 0
            return
        _read(fd, 0.2)
    os.kill(pid, 9)
    pytest.fail("the app did not quit on q")
