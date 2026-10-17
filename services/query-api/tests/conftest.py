import pytest

from query_api.auth import mint

SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def token():
    return lambda user: {"Authorization": f"Bearer {mint(user, SECRET)}"}
