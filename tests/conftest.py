from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def raise_server_exceptions() -> bool:
    """Normally surface unexpected application errors as test failures."""
    return True


@pytest.fixture
def client(raise_server_exceptions: bool) -> Iterator[TestClient]:
    """Run startup and shutdown for each test, giving it a fresh cache."""
    with TestClient(
        main.app, raise_server_exceptions=raise_server_exceptions
    ) as test_client:
        yield test_client
