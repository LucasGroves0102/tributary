"""The access suite's fixtures. Helpers live in access_world.py and access_routes.py (on
sys.path here, like tests/adversarial's harness: test files can't import each other under
--import-mode=importlib)."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from access_world import APP_DSN, DATA_DSN, World, make_world  # noqa: E402


@pytest.fixture(scope="module", params=["catalog_open", "catalog_closed"])
def world(request: Any) -> Iterator[World]:
    """One world per test module per catalog state (open: every signed-in user reads every
    company, so layer 1 says yes to everyone; closed: only private grants)."""
    if not (DATA_DSN and APP_DSN):
        pytest.skip("TEST_DATABASE_URL and APP_TEST_DATABASE_URL not set")
    with make_world(request.param == "catalog_open") as w:
        yield w
