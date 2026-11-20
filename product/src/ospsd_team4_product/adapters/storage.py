"""Storage adapter for the `check_doc` and `write_standup_doc` steps.

The shared contract (cloud-storage-api 0.3.1) has no upsert, so the doc is
written as:

1. `replace`. Success: done.
2. `ConflictError` (the file changed while replacing): `replace` once more.
3. `NotFoundError`: `create`.
4. `ConflictError` from that `create` (someone created it in between):
   `replace` once more.
5. `NotFoundError` from that `create`: the doc folder does not exist. The
   contract does not promise that `create` makes missing parent folders, so
   the folder is created once at setup, and its absence is reported as
   `service_not_configured` (an operator has to create it).

Anything else fails the step. The same plan always gives the same bytes, so
repeating the whole write is safe: a provider failure whose outcome is
unknown (`ProviderError`, or an unexpected exception) is therefore
`retryable` as well as `may_have_applied`, unlike the calendar's uncertain
create.

The error mapping is documented in product/docs/ERRORS.md ("check_doc
(storage)" and "write_standup_doc (storage)"). Keep those tables in sync with
`_MAPPING`.
"""

import logging
from dataclasses import dataclass
from typing import Final

from cloud_storage_api import (
    ConflictError,
    DocumentStorage,
    FileInfo,
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

from ospsd_team4_product.errors import Step, StepError

logger = logging.getLogger(__name__)

STEP: Final[Step] = "write_standup_doc"
CHECK_DOC_STEP: Final[Step] = "check_doc"

# (code, message, retryable, may_have_applied), most specific class first.
_MAPPING: tuple[tuple[type[StorageError], ErrorCode, str, bool, bool], ...] = (
    (
        InvalidRequestError,
        ErrorCode.INVALID_INPUT,
        "Storage rejected the request",
        False,
        False,
    ),
    (
        PermissionDeniedError,
        ErrorCode.ACCESS_DENIED,
        "Storage denied access",
        False,
        False,
    ),
    (
        NotFoundError,
        ErrorCode.NOT_FOUND,
        "The standup doc was not found",
        False,
        False,
    ),
    (
        ConflictError,
        ErrorCode.PRECONDITION_FAILED,
        "The standup doc kept changing while it was written",
        False,
        False,
    ),
    (
        FileTooLargeError,
        ErrorCode.INVALID_INPUT,
        "The standup doc is too large for storage",
        False,
        False,
    ),
    (
        InvalidOperationError,
        ErrorCode.INVALID_INPUT,
        "Storage refused the operation",
        False,
        False,
    ),
    (
        ProviderUnavailableError,
        ErrorCode.PROVIDER_ERROR,
        "Storage is currently unavailable",
        True,
        True,
    ),
    (
        ProviderError,
        ErrorCode.PROVIDER_ERROR,
        "Storage returned an unusable response",
        True,
        True,
    ),
)


def folder_missing() -> StepError:
    return StepError(
        STEP,
        ErrorCode.SERVICE_NOT_CONFIGURED,
        "The standup doc folder does not exist; an operator must create it",
        cause="storage_doc_folder_missing",
    )


@dataclass(frozen=True)
class StoredDoc:
    path: str
    id: str | None


def doc_exists_already() -> StepError:
    return StepError(
        CHECK_DOC_STEP,
        ErrorCode.PRECONDITION_FAILED,
        "A standup doc already exists for this assignee and day; "
        "replay the earlier response instead",
        cause="doc_exists",
    )


def translate(exc: Exception, step: Step = STEP) -> StepError:
    """Map a storage failure; on the read-only `check_doc` step nothing applied."""
    wrote = step == STEP
    if isinstance(exc, StorageError):
        for error_type, code, message, retryable, may_have_applied in _MAPPING:
            if isinstance(exc, error_type):
                return StepError(
                    step,
                    code,
                    message,
                    retryable=retryable,
                    may_have_applied=may_have_applied and wrote,
                    cause=f"storage_{exc.code}",
                )
    logger.error("storage raised an unexpected exception: %s", type(exc).__name__)
    return StepError(
        step,
        ErrorCode.PROVIDER_ERROR,
        "Storage returned an unusable response",
        retryable=True,
        may_have_applied=wrote,
        cause=type(exc).__name__,
    )


class StorageAdapter:
    def __init__(self, storage: DocumentStorage) -> None:
        self._storage = storage

    def check_doc_absent(self, path: str) -> None:
        """Raise StepError (412 on `check_doc`) if a file exists at `path`."""
        try:
            self._storage.get_metadata(path)
        except NotFoundError:
            return
        except Exception as exc:  # noqa: BLE001  (every failure is translated)
            raise translate(exc, CHECK_DOC_STEP) from None
        raise doc_exists_already()

    def write_doc(self, path: str, content: bytes) -> StoredDoc:
        """Create or replace the file at `path` (an already validated path)."""
        try:
            info = self._upsert(path, content)
        except StepError:
            raise
        except Exception as exc:  # noqa: BLE001  (every failure is translated)
            raise translate(exc) from None
        return StoredDoc(path=info.path, id=info.id)

    def _upsert(self, path: str, content: bytes) -> FileInfo:
        def replace() -> FileInfo:
            return self._storage.replace(path, content)

        try:
            return replace()
        except ConflictError:
            return replace()
        except NotFoundError:
            pass
        try:
            return self._storage.create(path, content)
        except ConflictError:
            return replace()
        except NotFoundError:
            raise folder_missing() from None
