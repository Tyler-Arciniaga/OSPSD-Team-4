"""The in-memory DocumentStorage follows the shared contract's documented rules."""

import pytest
from cloud_storage_api import (
    ConflictError,
    InvalidOperationError,
    InvalidRequestError,
    NotFoundError,
)

from ospsd_team4_product.fakes import InMemoryDocumentStorage


@pytest.fixture
def storage() -> InMemoryDocumentStorage:
    return InMemoryDocumentStorage()


def test_round_trip(storage: InMemoryDocumentStorage) -> None:
    created = storage.create("/a/b/doc.md", b"one")
    assert (created.name, created.path, created.size) == ("doc.md", "/a/b/doc.md", 3)
    assert created.modified_at is not None and created.modified_at.endswith("Z")

    replaced = storage.replace("/a/b/doc.md", b"two!")
    assert replaced.id == created.id and replaced.size == 4
    assert storage.download("/a/b/doc.md") == b"two!"
    assert storage.get_metadata("/a/b/doc.md").etag == replaced.etag

    storage.delete("/a/b/doc.md")
    with pytest.raises(NotFoundError):
        storage.get_metadata("/a/b/doc.md")


def test_create_existing_conflicts_and_replace_missing_never_creates(
    storage: InMemoryDocumentStorage,
) -> None:
    storage.create("/doc.md", b"x")
    with pytest.raises(ConflictError):
        storage.create("/doc.md", b"y")
    with pytest.raises(NotFoundError):
        storage.replace("/other.md", b"y")
    assert "/other.md" not in storage.files


@pytest.mark.parametrize(
    "path", ["", "/", "doc.md", "/a/", "/a//b", "/a/./b", "/a/../b"]
)
def test_bad_paths_are_rejected(storage: InMemoryDocumentStorage, path: str) -> None:
    with pytest.raises(InvalidRequestError):
        storage.create(path, b"x")


def test_list_files_returns_sorted_direct_children(
    storage: InMemoryDocumentStorage,
) -> None:
    storage.create("/s/b.md", b"x")
    storage.create("/s/a.md", b"x")
    storage.create("/s/day/c.md", b"x")

    entries = storage.list_files("/s")

    assert [(e.path, e.type) for e in entries] == [
        ("/s/a.md", "file"),
        ("/s/b.md", "file"),
        ("/s/day", "folder"),
    ]
    with pytest.raises(InvalidOperationError):
        storage.list_files("/s/a.md")


def test_move_and_folder_delete(storage: InMemoryDocumentStorage) -> None:
    storage.create("/s/a.md", b"x")
    storage.create("/s/b.md", b"y")
    with pytest.raises(ConflictError):
        storage.move("/s/a.md", "/s/b.md")

    moved = storage.move("/s/a.md", "/t/a.md")
    assert moved.path == "/t/a.md"

    storage.delete("/s")
    assert set(storage.files) == {"/t/a.md"}
    storage.check_health()
