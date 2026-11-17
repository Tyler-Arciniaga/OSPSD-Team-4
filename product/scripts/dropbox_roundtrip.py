"""Live round trip against the configured storage provider (human-run).

Checks the shared storage contract operations the product relies on, and
answers the open question from the brief: does `create` make missing parent
folders? The standup doc lives at /standups/<day>/<assignee>.md, so if it
does not, the product must fall back to a flat path.

Usage, from product/, with the Dropbox test account's credentials exported
in the shell (never in a file in the repo):

    export DROPBOX_APP_KEY=... DROPBOX_APP_SECRET=... DROPBOX_REFRESH_TOKEN=...
    uv run python scripts/dropbox_roundtrip.py

STORAGE_PROVIDER selects the implementation, as for the service; it defaults
to Team 3's `dropbox_client_impl:DropboxDocumentStorage`. Everything is
written under a new folder, /ospsd-team4-roundtrip-<random>, which is deleted
at the end.

The output is a Markdown table to paste into
product/docs/evidence/dropbox-roundtrip.md. It names exception classes and
error codes only, never provider messages, so it cannot print a credential.
Exit status: 0 if every check passed, 1 otherwise.
"""

import os
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cloud_storage_api import (
    ConflictError,
    DocumentStorage,
    NotFoundError,
    StorageError,
)

from ospsd_team4_product.adapters.storage import StorageAdapter
from ospsd_team4_product.config import (
    STORAGE_PROVIDER,
    ConfigError,
    resolve_import_path,
)
from ospsd_team4_product.errors import StepError

DEFAULT_PROVIDER = "dropbox_client_impl:DropboxDocumentStorage"


@dataclass
class Check:
    name: str
    expected: str
    result: str = "not run"
    passed: bool = False


def describe(exc: BaseException) -> str:
    """Class and code only; provider messages may contain paths or tokens."""
    if isinstance(exc, StorageError):
        return f"{type(exc).__name__} ({exc.code})"
    if isinstance(exc, StepError):
        return f"StepError ({exc.code.value})"
    return type(exc).__name__


def build_storage(environ: Mapping[str, str]) -> DocumentStorage:
    settings = {STORAGE_PROVIDER: environ.get(STORAGE_PROVIDER) or DEFAULT_PROVIDER}
    storage = resolve_import_path(STORAGE_PROVIDER, settings)()
    if not isinstance(storage, DocumentStorage):
        raise ConfigError(f"{STORAGE_PROVIDER} must build a DocumentStorage")
    return storage


def run(storage: DocumentStorage, root: str) -> list[Check]:
    flat = f"{root}-flat.md"
    nested = f"{root}/standups/2026-11-18/roundtrip.md"
    upserted = f"{root}/standups/2026-11-19/roundtrip.md"
    checks: list[Check] = []

    def check(
        name: str,
        expected: str,
        action: Callable[[], object],
        verify: Callable[[object], bool] = lambda _: True,
        raises: type[Exception] | None = None,
    ) -> bool:
        item = Check(name, expected)
        checks.append(item)
        try:
            value = action()
        except Exception as exc:  # noqa: BLE001  (recorded by class only)
            item.result = describe(exc)
            item.passed = raises is not None and isinstance(exc, raises)
            return item.passed
        item.result = "ok"
        item.passed = raises is None and verify(value)
        if raises is not None:
            item.result = "no error"
        elif not item.passed:
            item.result = "unexpected result"
        return item.passed

    # The single-file operations, on a path directly under the root.
    check(
        "create",
        "FileInfo for the new file",
        lambda: storage.create(flat, b"v1"),
        lambda info: getattr(info, "path", None) == flat,
    )
    check(
        "create existing",
        "ConflictError",
        lambda: storage.create(flat, b"again"),
        raises=ConflictError,
    )
    check(
        "replace",
        "FileInfo with the new size",
        lambda: storage.replace(flat, b"version two"),
        lambda info: getattr(info, "size", None) == len(b"version two"),
    )
    check(
        "download",
        "the replaced bytes",
        lambda: storage.download(flat),
        lambda content: content == b"version two",
    )
    check(
        "get_metadata",
        "size and an ID",
        lambda: storage.get_metadata(flat),
        lambda info: (
            getattr(info, "size", None) == len(b"version two")
            and getattr(info, "id", None) is not None
        ),
    )
    check("delete", "no error", lambda: storage.delete(flat))
    check(
        "download deleted",
        "NotFoundError",
        lambda: storage.download(flat),
        raises=NotFoundError,
    )
    check(
        "replace missing",
        "NotFoundError (the doc upsert relies on it)",
        lambda: storage.replace(flat, b"x"),
        raises=NotFoundError,
    )

    # The open question: are missing parent folders created?
    check(
        "create nested (parents missing)",
        "FileInfo; parent folders created",
        lambda: storage.create(nested, b"nested"),
        lambda info: getattr(info, "path", None) == nested,
    )
    check(
        "download nested",
        "the nested bytes",
        lambda: storage.download(nested),
        lambda content: content == b"nested",
    )

    # The product's own doc write (replace, then create on NotFound), twice:
    # the first call creates, the second replaces.
    adapter = StorageAdapter(storage)
    check(
        "doc upsert (new file)",
        "created",
        lambda: adapter.write_doc(upserted, b"# Standup\n"),
        lambda doc: getattr(doc, "path", None) == upserted,
    )
    check(
        "doc upsert (existing file)",
        "replaced",
        lambda: adapter.write_doc(upserted, b"# Standup again\n"),
        lambda doc: getattr(doc, "path", None) == upserted,
    )
    check(
        "download upserted",
        "the second write's bytes",
        lambda: storage.download(upserted),
        lambda content: content == b"# Standup again\n",
    )
    return checks


def cleanup(storage: DocumentStorage, root: str) -> str:
    results = []
    for path in (root, f"{root}-flat.md"):
        try:
            storage.delete(path)
            results.append(f"{path}: deleted")
        except NotFoundError:
            results.append(f"{path}: absent")
        except Exception as exc:  # noqa: BLE001  (recorded by class only)
            results.append(f"{path}: {describe(exc)}, delete it by hand")
    return "; ".join(results)


def report(checks: list[Check], root: str, cleaned: str) -> str:
    nested = next(c for c in checks if c.name.startswith("create nested"))
    lines = [
        f"Run root: `{root}`",
        "",
        "| Check | Expected | Result | Pass |",
        "| ----- | -------- | ------ | ---- |",
        *(
            f"| {c.name} | {c.expected} | {c.result} | {'yes' if c.passed else 'no'} |"
            for c in checks
        ),
        "",
        "Parent folders created by `create`: "
        + ("yes" if nested.passed else "no (use the flat-path fallback)"),
        f"Cleanup: {cleaned}",
    ]
    return "\n".join(lines)


def main(environ: Mapping[str, str] = os.environ) -> int:
    try:
        storage = build_storage(environ)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001  (e.g. DropboxAuthError)
        print(
            f"Could not build the storage provider: {describe(exc)}. "
            "Check that the Dropbox variables are exported.",
            file=sys.stderr,
        )
        return 1
    root = f"/ospsd-team4-roundtrip-{uuid.uuid4().hex[:8]}"
    checks = run(storage, root)
    print(report(checks, root, cleanup(storage, root)))
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
