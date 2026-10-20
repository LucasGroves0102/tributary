"""Run the citation-accuracy eval against the live stack: make citations [CASES="fin-01 ..."]"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "adversarial"))
sys.path.insert(0, str(HERE))
import adversarial_harness as h  # noqa: E402
from citation_cases import CASES  # noqa: E402
from query_api.auth import mint  # noqa: E402
from tributary_agent.agent import Agent  # noqa: E402
from tributary_agent.tools import MetricTools, QueryApi  # noqa: E402

METRIC_TOOLS = MetricTools()


def cites(v: dict, answer: str, citations: dict) -> bool:
    """Data answers name at least one filing a tool returned and state freshness."""
    return not v["used_data"] or (v["freshness"] and any(a in answer for a in citations))


def run_case(case, client, model, base_url, secret) -> dict:
    cid, user, question = case
    messages = h.RecordingMessages(client.messages)
    query_api = QueryApi(base_url, mint(user, secret, 600))
    api = h.RecordingApi(query_api, max_chars=None)
    answer = Agent(messages, api, model, tools=METRIC_TOOLS.get(query_api)).ask(question)
    rec = {"id": cid, "user": user, "prompt": question, "model": model, "answer": answer.text,
           "usage": asdict(answer.usage), "cost_usd": answer.cost_usd,
           "citations": answer.citations, "verification": answer.verification,
           "tool_calls": api.calls, "responses": messages.responses}
    (HERE / "recordings" / f"{cid}.json").write_text(h.dumps(rec))
    v = answer.verification
    bad = [f["text"] for f in v["figures"] if f["status"] == "unverified"]
    print(f"{cid:7} {user:6} {'ok' if v['ok'] else 'FAIL':5} {len(v['figures']):3} figures "
          f"{bad or ''}{v['unverified_accessions'] or ''}", flush=True)
    return rec


def report(recs: list[dict], model: str) -> str:
    figures = [f for r in recs for f in r["verification"]["figures"]]
    by_status = {s: sum(f["status"] == s for f in figures)
                 for s in ("verified", "derived", "quoted", "unverified")}
    data = [r for r in recs if r["verification"]["used_data"]]
    ok = [r for r in recs if r["verification"]["ok"]]
    citing = [r for r in data if cites(r["verification"], r["answer"], r["citations"])]
    accessions = sum(len(r["verification"]["unverified_accessions"]) for r in recs)
    cost = sum(r["cost_usd"] or 0 for r in recs)
    lines = [
        "# Citation accuracy", "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC, `{model}`.", "",
        "| | |", "|---|---|",
        f"| Questions | {len(recs)} |",
        f"| **Answers fully verified** | **{len(ok)} of {len(recs)}** |",
        f"| Answers using data that cite a filing and freshness | {len(citing)} of {len(data)} |",
        f"| Dollar figures checked | {len(figures)} |",
        f"| Matched a returned value | {by_status['verified']} |",
        f"| Derived from returned values (differences, sums) | {by_status['derived']} |",
        f"| Quoted from returned filing text | {by_status['quoted']} |",
        f"| **Not found in the data** | **{by_status['unverified']}** |",
        f"| Accession numbers not returned by a tool | {accessions} |",
        f"| Cost | ${cost:.3f} |", "",
        "| Case | User | Verified | Figures | Unverified | Question |", "|---|---|---|---|---|---|",
    ]
    for r in recs:
        v = r["verification"]
        bad = ", ".join(f["text"] for f in v["figures"] if f["status"] == "unverified") or "—"
        lines.append(f"| {r['id']} | {r['user']} | {'yes' if v['ok'] else '**no**'} | "
                     f"{len(v['figures'])} | {bad} | {r['prompt']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    base_url = os.environ.get("QUERY_API_URL", "http://localhost:8000")
    try:
        httpx.get(f"{base_url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"Query API not reachable at {base_url}; start it first (make up-all)")
    model = os.environ.get("AGENT_MODEL", "claude-haiku-5-5")
    only = set(sys.argv[1:])
    cases = [c for c in CASES if not only or c[0] in only]
    client = anthropic.Anthropic()
    secret = os.environ["QUERY_API_JWT_SECRET"]
    print(f"{len(cases)} questions with {model}")
    with ThreadPoolExecutor(4) as pool:
        recs = list(pool.map(lambda c: run_case(c, client, model, base_url, secret), cases))
    if not only:
        (HERE / "report.md").write_text(report(recs, model))
    failed = [r["id"] for r in recs if not r["verification"]["ok"]]
    print(f"\n{len(recs) - len(failed)} of {len(recs)} fully verified; failed: {failed or 'none'}")


if __name__ == "__main__":
    main()
