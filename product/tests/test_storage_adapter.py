"""Storage adapter: the replace-then-create upsert and error mapping."""

import pytest
from cloud_storage_api import (
    ConflictError,
    FileTooLargeError,
    InvalidOperationError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnavailableError,
    StorageError,
)
from issue_tracker_contract.errors import ErrorCode

from ospsd_team4_product.adapters.storage import StorageAdapter, StoredDoc
from ospsd_team4_product.errors import StepError
from ospsd_team4_product.fakes import InMemoryDocumentStorage

PATH = "/standups/2026-11-18-tyler.md"
SECRET = "Dropbox said /secret/path token=abc"


@pytest.fixture
def storage() -> InMemoryDocumentStorage:
    return InMemoryDocumentStorage()


def calls(storage: InMemoryDocumentStorage) -> dict[str, int]:
    return {op: storage.failures.calls(op) for op in ("replace", "create")}


def test_replace_succeeds(storage: InMemoryDocumentStorage) -> None:
    storage.files[PATH] = b"old"
    storage._ids[PATH] = "fake-file-0"

    doc = StorageAdapter(storage).write_doc(PATH, b"new")

    assert doc == StoredDoc(path=PATH, id="fake-file-0")
    assert storage.files[PATH] == b"new"
    assert calls(storage) == {"replace": 1, "create": 0}


def test_not_found_then_create(storage: InMemoryDocumentStorage) -> None:
    doc = StorageAdapter(storage).write_doc(PATH, b"new")

    assert doc.path == PATH and doc.id is not None
    assert storage.files[PATH] == b"new"
    assert calls(storage) == {"replace": 1, "create": 1}


def test_create_conflict_then_replace(storage: InMemoryDocumentStorage) -> None:
    # Someone else creates the file between our replace and create.
    storage.failures.fail("create", ConflictError(SECRET))
    original_create = storage.create

    def racing_create(path: str, content: bytes) -> object:
        storage.files[path] = b"theirs"
        storage._ids[path] = "fake-file-theirs"
        return original_create(path, content)

    storage.create = racing_create  # type: ignore[method-assign,assignment]

    doc = StorageAdapter(storage).write_doc(PATH, b"ours")

    assert storage.files[PATH] == b"ours"
    assert doc.id == "fake-file-theirs"
    assert calls(storage) == {"replace": 2, "create": 1}


def test_replace_conflict_is_retried_once(storage: InMemoryDocumentStorage) -> None:
    storage.files[PATH] = b"old"
    storage._ids[PATH] = "fake-file-0"
    storage.failures.fail("replace", ConflictError(SECRET))

    StorageAdapter(storage).write_doc(PATH, b"new")

    assert storage.files[PATH] == b"new"
    assert calls(storage) == {"replace": 2, "create": 0}


def test_second_replace_conflict_fails_the_step(
    storage: InMemoryDocumentStorage,
) -> None:
    storage.files[PATH] = b"old"
    storage._ids[PATH] = "fake-file-0"
    storage.failures.fail("replace", ConflictError(SECRET), on_call=1)
    storage.failures.fail("replace", ConflictError(SECRET), on_call=2)

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.code == ErrorCode.PRECONDITION_FAILED
    assert calls(storage) == {"replace": 2, "create": 0}


def test_provider_unavailable_fails_the_step_as_retryable(
    storage: InMemoryDocumentStorage,
) -> None:
    storage.failures.fail("replace", ProviderUnavailableError(SECRET))

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.step == "write_standup_doc"
    assert raised.value.code == ErrorCode.PROVIDER_ERROR
    assert raised.value.retryable is True
    assert calls(storage) == {"replace": 1, "create": 0}


def test_bad_path_is_rejected_before_any_provider_call(
    storage: InMemoryDocumentStorage,
) -> None:
    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc("/standups/../x.md", b"new")

    assert raised.value.code == ErrorCode.INVALID_INPUT
    assert calls(storage) == {"replace": 0, "create": 0}


class OddStorageError(StorageError):
    code = "odd"


@pytest.mark.parametrize(
    ("error", "code", "retryable", "may_have_applied"),
    [
        (InvalidRequestError(SECRET), ErrorCode.INVALID_INPUT, False, False),
        (PermissionDeniedError(SECRET), ErrorCode.ACCESS_DENIED, False, False),
        (FileTooLargeError(SECRET), ErrorCode.INVALID_INPUT, False, False),
        (InvalidOperationError(SECRET), ErrorCode.INVALID_INPUT, False, False),
        (ProviderError(SECRET), ErrorCode.PROVIDER_ERROR, True, True),
        (ProviderUnavailableError(SECRET), ErrorCode.PROVIDER_ERROR, True, True),
        (OddStorageError(SECRET), ErrorCode.PROVIDER_ERROR, True, True),
        (StorageError(SECRET), ErrorCode.PROVIDER_ERROR, True, True),
        (RuntimeError(SECRET), ErrorCode.PROVIDER_ERROR, True, True),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else None,
)
def test_errors_are_mapped_without_leaking_messages(
    storage: InMemoryDocumentStorage,
    error: Exception,
    code: ErrorCode,
    retryable: bool,
    may_have_applied: bool,
) -> None:
    storage.failures.fail("replace", error)

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.step == "write_standup_doc"
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.may_have_applied is may_have_applied
    assert SECRET not in raised.value.message
    assert raised.value.__cause__ is None


def test_missing_doc_folder_is_a_configuration_failure() -> None:
    storage = InMemoryDocumentStorage(folders=())

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.step == "write_standup_doc"
    assert raised.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert raised.value.status == 503
    assert raised.value.retryable is False
    assert raised.value.may_have_applied is False
    assert storage.files == {}
    assert calls(storage) == {"replace": 1, "create": 1}


def test_not_found_from_create_is_reported_without_leaking(
    storage: InMemoryDocumentStorage,
) -> None:
    storage.failures.fail("create", NotFoundError(SECRET))

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.code == ErrorCode.SERVICE_NOT_CONFIGURED
    assert SECRET not in raised.value.message
    assert raised.value.__cause__ is None


def test_not_found_after_create_conflict_is_reported(
    storage: InMemoryDocumentStorage,
) -> None:
    # Created by someone else, then deleted before our final replace.
    storage.failures.fail("create", ConflictError(SECRET))

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).write_doc(PATH, b"new")

    assert raised.value.code == ErrorCode.NOT_FOUND
    assert calls(storage) == {"replace": 2, "create": 1}


# check_doc (F5): a read before any event is booked


def test_absent_doc_passes_the_check(storage: InMemoryDocumentStorage) -> None:
    StorageAdapter(storage).check_doc_absent(PATH)

    assert storage.failures.calls("get_metadata") == 1


def test_missing_doc_folder_passes_the_check() -> None:
    """The folder is reported later, by write_standup_doc (503)."""
    StorageAdapter(InMemoryDocumentStorage(folders=())).check_doc_absent(PATH)


def test_existing_doc_fails_the_check(storage: InMemoryDocumentStorage) -> None:
    storage.create(PATH, b"old")

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).check_doc_absent(PATH)

    assert raised.value.step == "check_doc"
    assert raised.value.code == ErrorCode.PRECONDITION_FAILED
    assert raised.value.status == 412
    assert raised.value.retryable is False
    assert raised.value.may_have_applied is False
    assert storage.files[PATH] == b"old"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (PermissionDeniedError(SECRET), ErrorCode.ACCESS_DENIED, False),
        (InvalidOperationError(SECRET), ErrorCode.INVALID_INPUT, False),
        (ProviderError(SECRET), ErrorCode.PROVIDER_ERROR, True),
        (ProviderUnavailableError(SECRET), ErrorCode.PROVIDER_ERROR, True),
        (RuntimeError(SECRET), ErrorCode.PROVIDER_ERROR, True),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else None,
)
def test_check_failures_are_reads_that_applied_nothing(
    storage: InMemoryDocumentStorage,
    error: Exception,
    code: ErrorCode,
    retryable: bool,
) -> None:
    storage.failures.fail("get_metadata", error)

    with pytest.raises(StepError) as raised:
        StorageAdapter(storage).check_doc_absent(PATH)

    assert raised.value.step == "check_doc"
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.may_have_applied is False
    assert SECRET not in raised.value.message
    assert raised.value.__cause__ is None
