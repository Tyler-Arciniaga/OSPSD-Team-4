"""The live round-trip script, run against the in-memory storage.

This proves the script's checks and report, not Dropbox behavior: the live
result is recorded separately in docs/evidence/.
"""

import pytest
from cloud_storage_api import InvalidRequestError
from scripts import dropbox_roundtrip

from ospsd_team4_product.fakes import InMemoryDocumentStorage

FAKE = {"STORAGE_PROVIDER": "ospsd_team4_product.fakes:InMemoryDocumentStorage"}


def test_every_check_passes_against_the_fake(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dropbox_roundtrip.main(FAKE) == 0

    out = capsys.readouterr().out
    assert "| no |" not in out
    assert "Parent folders created by `create`: yes" in out
    assert "deleted" in out


def test_everything_is_cleaned_up() -> None:
    storage = InMemoryDocumentStorage()
    root = "/ospsd-team4-roundtrip-test"

    dropbox_roundtrip.run(storage, root)
    dropbox_roundtrip.cleanup(storage, root)

    assert storage.files == {}


def test_a_storage_without_parent_folders_is_reported() -> None:
    class FlatStorage(InMemoryDocumentStorage):
        def create(self, path: str, content: bytes) -> object:  # type: ignore[override]
            if path.count("/") > 1:
                raise InvalidRequestError("secret-path-detail")
            return super().create(path, content)

    root = "/ospsd-team4-roundtrip-test"
    checks = dropbox_roundtrip.run(FlatStorage(), root)
    text = dropbox_roundtrip.report(checks, root, "n/a")

    assert "no (use the flat-path fallback)" in text
    assert "InvalidRequestError (invalid_request)" in text
    assert "secret-path-detail" not in text


def test_missing_credentials_are_reported_without_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        dropbox_roundtrip.main({"STORAGE_PROVIDER": "tests.helpers:failing_factory"})
        == 1
    )

    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "secret" not in err
