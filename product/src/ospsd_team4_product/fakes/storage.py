"""In-memory DocumentStorage for tests and the disposable environment.

Select it with `STORAGE_PROVIDER=ospsd_team4_product.fakes:InMemoryDocumentStorage`.
Folders are implicit: `create` makes missing parents, as Dropbox is expected
to (to be confirmed by the live round trip, WP6). Files live only in this
process.
"""

import hashlib
from datetime import UTC, datetime
from itertools import count

from cloud_storage_api import (
    ConflictError,
    DocumentStorage,
    FileInfo,
    InvalidOperationError,
    InvalidRequestError,
    NotFoundError,
)

from ospsd_team4_product.fakes._failures import FailureSchedule


def check_path(path: str) -> None:
    """The shared path rule; raises InvalidRequestError before any change."""
    if not path.startswith("/") or path == "/" or path.endswith("/"):
        raise InvalidRequestError("invalid path")
    if any(part in {"", ".", ".."} for part in path[1:].split("/")):
        raise InvalidRequestError("invalid path")


class InMemoryDocumentStorage(DocumentStorage):
    def __init__(self, *, always_fail: Exception | None = None) -> None:
        self.files: dict[str, bytes] = {}
        self.failures = FailureSchedule(always_fail)
        self._ids: dict[str, str] = {}
        self._next_id = count(1)

    def _folders(self) -> set[str]:
        return {parent for path in self.files for parent in _parents(path)}

    def _info(self, path: str) -> FileInfo:
        content = self.files[path]
        return FileInfo(
            id=self._ids[path],
            name=path.rsplit("/", 1)[1],
            path=path,
            size=len(content),
            modified_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            etag=hashlib.sha256(content).hexdigest()[:16],
            type=None,
        )

    def _require_file(self, path: str) -> None:
        if path in self._folders():
            raise InvalidOperationError("not a file")
        if path not in self.files:
            raise NotFoundError("not found")

    def create(self, path: str, content: bytes) -> FileInfo:
        check_path(path)
        self.failures.check("create")
        if path in self.files or path in self._folders():
            raise ConflictError("already exists")
        if any(parent in self.files for parent in _parents(path)):
            raise ConflictError("a parent is a file")
        self.files[path] = content
        self._ids[path] = f"fake-file-{next(self._next_id)}"
        return self._info(path)

    def replace(self, path: str, content: bytes) -> FileInfo:
        check_path(path)
        self.failures.check("replace")
        self._require_file(path)
        self.files[path] = content
        return self._info(path)

    def download(self, path: str) -> bytes:
        check_path(path)
        self.failures.check("download")
        self._require_file(path)
        return self.files[path]

    def get_metadata(self, path: str) -> FileInfo:
        check_path(path)
        self.failures.check("get_metadata")
        self._require_file(path)
        return self._info(path)

    def list_files(self, path: str) -> list[FileInfo]:
        check_path(path)
        self.failures.check("list_files")
        if path in self.files:
            raise InvalidOperationError("not a folder")
        if path not in self._folders():
            raise NotFoundError("not found")
        prefix = path + "/"
        entries = []
        for child in sorted(
            {
                prefix + p[len(prefix) :].split("/")[0]
                for p in self.files
                if p.startswith(prefix)
            }
        ):
            if child in self.files:
                info = self._info(child)
                info.type = "file"
            else:
                info = FileInfo(
                    id=None,
                    name=child.rsplit("/", 1)[1],
                    path=child,
                    size=None,
                    modified_at=None,
                    etag=None,
                    type="folder",
                )
            entries.append(info)
        return entries

    def move(self, from_path: str, to_path: str) -> FileInfo:
        check_path(from_path)
        check_path(to_path)
        self.failures.check("move")
        self._require_file(from_path)
        if to_path in self.files or to_path in self._folders():
            raise ConflictError("destination exists")
        self.files[to_path] = self.files.pop(from_path)
        self._ids[to_path] = self._ids.pop(from_path)
        return self._info(to_path)

    def delete(self, path: str) -> None:
        check_path(path)
        self.failures.check("delete")
        if path in self.files:
            del self.files[path]
            del self._ids[path]
            return
        if path not in self._folders():
            raise NotFoundError("not found")
        for child in [p for p in self.files if p.startswith(path + "/")]:
            del self.files[child]
            del self._ids[child]

    def check_health(self) -> None:
        self.failures.check("check_health")


def _parents(path: str) -> list[str]:
    parts = path.split("/")[1:-1]
    return ["/" + "/".join(parts[: i + 1]) for i in range(len(parts))]
