"""The system's IANA time zone name, the form's default time zone."""

import os
from collections.abc import Mapping
from pathlib import Path

from focus_day_tui.form import parse_zone


def system_timezone(
    environ: Mapping[str, str] = os.environ,
    localtime: Path = Path("/etc/localtime"),
    timezone_file: Path = Path("/etc/timezone"),
) -> str:
    """`TZ`, else the zone /etc/localtime links to, else /etc/timezone, else UTC.

    Python has no portable way to get the name, and the API needs a name,
    not an offset.
    """
    candidates = [environ.get("TZ", "").removeprefix(":")]
    try:
        target = os.readlink(localtime)
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(timezone_file.read_text(encoding="utf-8").strip())
    except OSError:
        pass
    for name in candidates:
        if name and parse_zone(name) is not None:
            return name
    return "UTC"
