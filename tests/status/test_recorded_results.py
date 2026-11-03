"""The status page's recorded-suite figures match the committed recordings (no network)."""

import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("recorded_results", HERE / "recorded_results.py")
assert spec and spec.loader
recorded_results = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorded_results)


def test_committed_json_matches_the_recordings():
    committed = json.loads(recorded_results.OUT.read_text())
    current = recorded_results.summarize()
    assert set(committed) == set(current)
    for name, section in current.items():
        got = {k: v for k, v in committed[name].items() if k not in ("commit", "committed")}
        assert got == section, f"{name} changed: run make status-results and commit the JSON"
        assert re.fullmatch(r"[0-9a-f]{7,40}", committed[name]["commit"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", committed[name]["committed"])


def test_summary_has_counts_and_dates_only():
    current = recorded_results.summarize()
    text = json.dumps(current)
    assert not re.search(r"\d{10}-\d{2}-\d{6}", text)  # no accession numbers
    assert not re.search(r"\b\d{10}\b", text)          # no company ids
    assert current["adversarial"]["attacks"] >= 50
