import socket
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def is_db_reachable() -> bool:
    """Quick socket check to verify if the Postgres database_url is accepting connections."""
    try:
        parsed = urlparse(settings.database_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5433
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Test client fixture that runs the app lifespan against the real database.

    Gracefully skips integration tests if the Postgres container is not running.
    """
    if not is_db_reachable():
        msg = "Postgres container is not running on localhost:5433 (start with 'make db-up')."
        pytest.skip(msg)

    with TestClient(app) as c:
        yield c
