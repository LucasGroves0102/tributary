"""Summarizes the recorded suites for the public status page (Query API `/v1/status/data`).

    python tests/status/recorded_results.py   # or: make status-results

writes services/query-api/src/query_api/data/recorded_results.json from what is committed:
the adversarial and citation recordings (the same files CI replays), their reports' run date and
model, and the golden 10-K figures CSV. Each section carries the commit that last changed its
inputs, so run it after committing new recordings; it refuses while they have uncommitted
changes. test_recorded_results.py fails when the JSON no longer matches the recordings.

Nothing here calls the API or re-runs a suite: the figures are the last recorded run's.
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "services/query-api/src/query_api/data/recorded_results.json"
ADVERSARIAL = ROOT / "tests/adversarial"
CITATIONS = ROOT / "tests/citations"
GOLDEN = ROOT / "tests/golden/10k_figures.csv"
# first line under the title of report.md (adversarial/run.py, citations/run.py)
RUN_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC, `([^`]+)`\.$", re.M)

sys.path.insert(0, str(ADVERSARIAL))
sys.path.insert(0, str(CITATIONS))


def _recording(folder: Path, cid: str) -> dict:
    return json.loads((folder / "recordings" / f"{cid}.json").read_text())


def _run(report: Path) -> dict:
    """When the last full run was recorded (report.md's header; a run of selected cases
    rewrites their recordings but not the report, so `models` comes from the recordings)."""
    m = RUN_HEADER.search(report.read_text())
    if not m:
        raise ValueError(f"{report}: no run header")
    return {"recorded_at": f"{m[1]}T{m[2]}:00Z"}


def adversarial() -> dict:
    import adversarial_harness as h
    from adversarial_cases import ATTACKS, CONTROLS

    attacks = [_recording(ADVERSARIAL, c[0]) for c in ATTACKS]
    controls = [_recording(ADVERSARIAL, c[0]) for c in CONTROLS]
    return {
        **_run(ADVERSARIAL / "report.md"),
        "models": sorted({r["model"] for r in attacks + controls}),
        "attacks": len(attacks),
        "categories": len({c[2] for c in ATTACKS}),
        "leaks": sum(h.is_leak(r["checks"]) for r in attacks),
        "controls": len(controls),
        "controls_answered": sum(h.control_ok(r) and not h.is_leak(r["checks"])
                                 for r in controls),
        "report": "tests/adversarial/report.md",
    }


def _cites(rec: dict) -> bool:
    """citations/run.py `cites`: a data answer names a filing a tool returned and states
    freshness (that module imports the live-run clients, so it is restated here)."""
    v = rec["verification"]
    return not v["used_data"] or bool(v["freshness"] and any(a in rec["answer"]
                                                             for a in rec["citations"]))


def citations() -> dict:
    from citation_cases import CASES

    recs = [_recording(CITATIONS, c[0]) for c in CASES]
    figures = [f for r in recs for f in r["verification"]["figures"]]
    data = [r for r in recs if r["verification"]["used_data"]]
    return {
        **_run(CITATIONS / "report.md"),
        "models": sorted({r["model"] for r in recs}),
        "questions": len(recs),
        "verified_answers": sum(bool(r["verification"]["ok"]) for r in recs),
        "data_answers": len(data),
        "data_answers_citing": sum(_cites(r) for r in data),
        "figures": len(figures),
        "figures_traced": sum(f["status"] != "unverified" for f in figures),
        "untraced_accessions": sum(len(r["verification"]["unverified_accessions"])
                                   for r in recs),
        "report": "tests/citations/report.md",
    }


def golden() -> dict:
    with GOLDEN.open(newline="") as f:
        rows = list(csv.DictReader(f))
    checks: dict[str, int] = {}
    for r in rows:
        checks[r["check"]] = checks.get(r["check"], 0) + 1
    return {
        "figures": len(rows),
        "companies": len({r["cik"] for r in rows}),
        "filings": len({r["accession_no"] for r in rows}),
        "checks": dict(sorted(checks.items())),
        "hand_checked": sum(bool(r["verified_by"].strip()) for r in rows),
        "readme": "tests/golden/README.md",
    }


def summarize() -> dict:
    """Everything except the commits (which need git): what the drift test compares."""
    return {"adversarial": adversarial(), "citations": citations(), "golden": golden()}


INPUTS = {"adversarial": [ADVERSARIAL / "recordings", ADVERSARIAL / "report.md",
                          ADVERSARIAL / "adversarial_cases.py"],
          "citations": [CITATIONS / "recordings", CITATIONS / "report.md",
                        CITATIONS / "citation_cases.py"],
          "golden": [GOLDEN]}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def main() -> None:
    summary = summarize()
    for name, paths in INPUTS.items():
        rel = [str(p.relative_to(ROOT)) for p in paths]
        if _git("status", "--porcelain", "--", *rel):
            sys.exit(f"{name}: uncommitted changes in {', '.join(rel)}; commit them first")
        sha, day = _git("log", "-1", "--format=%h %cs", "--", *rel).split()
        summary[name] |= {"commit": sha, "committed": day}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
