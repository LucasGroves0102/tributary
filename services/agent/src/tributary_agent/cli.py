"""make ask USER=alice Q="..." """

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

import anthropic

from tributary_agent.agent import Agent
from tributary_agent.tools import QueryApi

log = logging.getLogger("tributary_agent")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ask Tributary's agent a question.")
    ap.add_argument("question")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)

    agent = Agent(
        messages=anthropic.Anthropic().messages,
        api=QueryApi(os.environ.get("QUERY_API_URL", "http://localhost:8000"),
                     os.environ["AGENT_TOKEN"]),
        model=os.environ.get("AGENT_MODEL", "claude-haiku-5-5"),
        max_rounds=int(os.environ.get("AGENT_MAX_ROUNDS", "6")),
        effort=os.environ.get("AGENT_EFFORT") or None,
    )
    answer = agent.ask(args.question)

    if args.json:
        print(json.dumps({**asdict(answer), "cost_usd": answer.cost_usd}, indent=2))
        return
    print(answer.text)
    for c in answer.tool_calls:
        log.info("  tool %s %s%s", c.name, json.dumps(c.input), "  -> error" if c.is_error else "")
    u = answer.usage
    cost = f"${answer.cost_usd:.4f}" if answer.cost_usd is not None else "unknown price"
    log.info("%s | %d rounds | in %d (cached %d) out %d | %s | stop=%s", answer.model,
             answer.rounds, u.input_tokens + u.cache_read_input_tokens
             + u.cache_creation_input_tokens, u.cache_read_input_tokens, u.output_tokens,
             cost, answer.stop)


if __name__ == "__main__":
    main()
