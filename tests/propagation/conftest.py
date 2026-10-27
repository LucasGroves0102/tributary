"""Shared by the propagation tests: a recorder for the measured seconds.

With PROPAGATION_RESULTS=<path>, every measurement is written there as JSON
({"scenario": [seconds, ...]}), rewritten after each one so a failing run still leaves what it
measured. CI prints it into the job summary (ci.yml, job propagation).
"""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def record():
    results: dict[str, list[float]] = {}
    path = os.environ.get("PROPAGATION_RESULTS")

    def add(scenario: str, seconds: float) -> None:
        results.setdefault(scenario, []).append(round(seconds, 3))
        if path:
            Path(path).write_text(json.dumps(results, indent=2) + "\n")

    return add
