"""Replays the recorded citation eval (make citations) and re-verifies every answer."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "adversarial"))
sys.path.insert(0, str(HERE))
import adversarial_harness as h  # noqa: E402
from citation_cases import CASES  # noqa: E402


def load(cid: str) -> dict:
    path = HERE / "recordings" / f"{cid}.json"
    if not path.exists():
        pytest.fail(f"no recording for {cid}: run make citations")
    return json.loads(path.read_text())


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_case(case):
    cid, user, question = case
    rec = load(cid)
    assert (rec["user"], rec["prompt"]) == (user, question), "case changed: run make citations"
    answer = h.replay(rec)
    assert answer.text == rec["answer"]
    assert answer.verification == rec["verification"]
    assert answer.verification["ok"], json.dumps(answer.verification, indent=1)
