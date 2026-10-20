"""Compare the Python and Rust parsers on a corpus (scripts/corpus.py): identical sections,
then MB/s, median and p95 parse time of each.

Usage: python scripts/bench.py CORPUS_DIR [--rust CMD] [--repeat 3]

CMD runs the release `parser-rs` binary (default: target/release/parser-rs). Times cover
parsing only (not reading or gunzipping) and are each document's best of --repeat runs, one
document in memory at a time.
"""

import argparse
import gzip
import json
import shlex
import statistics
import subprocess
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from filing_parser.sections import exhibit_section, parse

HERE = Path(__file__).resolve().parent.parent


def entries(corpus: Path) -> list[dict]:
    with (corpus / "manifest.jsonl").open() as f:
        return [json.loads(line) for line in f]


def python_parse(e: dict, raw: bytes) -> list[dict] | str:
    try:
        if "exhibit_type" in e:
            sections = [exhibit_section(e["exhibit_type"], e.get("description"), raw)]
        else:
            sections = parse(raw, e["form"])
    except Exception as exc:  # noqa: BLE001 - compared with the Rust error
        return f"error: {type(exc).__name__}"
    return [asdict(s) for s in sections]


def rust_lines(cmd: list[str], manifest: Path, bench: bool) -> Iterator[dict]:
    with manifest.open() as stdin, subprocess.Popen(
        cmd + (["--bench"] if bench else []), stdin=stdin, stdout=subprocess.PIPE, text=True
    ) as p:
        assert p.stdout is not None
        for line in p.stdout:
            yield json.loads(line)
    if p.returncode:
        raise SystemExit(f"{cmd} exited {p.returncode}")


def compare(corpus: Path, cmd: list[str]) -> int:
    diffs = 0
    es = entries(corpus)
    for e, r in zip(es, rust_lines(cmd, corpus / "manifest.jsonl", False), strict=True):
        assert r["id"] == e["id"]
        py = python_parse(e, gzip.decompress(Path(e["path"]).read_bytes()))
        rs = r.get("sections", f"error: {r.get('error')}")
        if isinstance(py, str) and isinstance(rs, str):
            continue  # both failed
        if py != rs:
            diffs += 1
            print(f"DIFF {e['id']}: {describe(py, rs)}")
    print(f"compared {len(es)} documents: {diffs} with differences")
    return diffs


def describe(py: list[dict] | str, rs: list[dict] | str) -> str:
    if isinstance(py, str) or isinstance(rs, str):
        outcome = [r if isinstance(r, str) else "ok" for r in (py, rs)]
        return f"python {outcome[0]}, rust {outcome[1]}"
    if len(py) != len(rs):
        return f"python keys {[s['key'] for s in py]}, rust keys {[s['key'] for s in rs]}"
    for a, b in zip(py, rs, strict=True):
        for k in a:
            if a[k] != b[k]:
                return f"section {a['key']} field {k}: {str(a[k])[:80]!r} vs {str(b[k])[:80]!r}"
    return "?"


def python_times(corpus: Path, repeat: int) -> dict[str, float]:
    best: dict[str, float] = {}
    for _ in range(repeat):
        for e in entries(corpus):
            raw = gzip.decompress(Path(e["path"]).read_bytes())
            t0 = time.perf_counter()
            python_parse(e, raw)
            t = time.perf_counter() - t0
            best[e["id"]] = min(t, best.get(e["id"], t))
    return best


def rust_times(corpus: Path, cmd: list[str], repeat: int) -> dict[str, float]:
    best: dict[str, float] = {}
    for _ in range(repeat):
        for r in rust_lines(cmd, corpus / "manifest.jsonl", True):
            best[r["id"]] = min(r["seconds"], best.get(r["id"], r["seconds"]))
    return best


def report(es: list[dict], py: dict[str, float], rs: dict[str, float]) -> None:
    groups = {"all": es}
    for form in ("10-K", "10-Q", "8-K"):
        groups[form] = [e for e in es if e.get("form") == form]
    groups["exhibits"] = [e for e in es if "exhibit_type" in e]
    print(f"{'inputs':<10}{'docs':>6}{'MB':>8}  {'parser':<7}{'MB/s':>8}{'median ms':>11}"
          f"{'p95 ms':>9}")
    for name, group in groups.items():
        if not group:
            continue
        mb = sum(e["bytes"] for e in group) / 1e6
        for parser, times in (("python", py), ("rust", rs)):
            ts = sorted(times[e["id"]] for e in group)
            p95 = ts[min(len(ts) - 1, round(0.95 * (len(ts) - 1)))]
            print(f"{name:<10}{len(group):>6}{mb:>8.1f}  {parser:<7}{mb / sum(ts):>8.1f}"
                  f"{statistics.median(ts) * 1000:>11.1f}{p95 * 1000:>9.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--rust", default=str(HERE / "target/release/parser-rs"))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--no-compare", action="store_true")
    args = ap.parse_args()
    cmd = shlex.split(args.rust)
    if not args.no_compare and compare(args.corpus, cmd):
        raise SystemExit(1)
    report(entries(args.corpus), python_times(args.corpus, args.repeat),
           rust_times(args.corpus, cmd, args.repeat))


if __name__ == "__main__":
    main()
