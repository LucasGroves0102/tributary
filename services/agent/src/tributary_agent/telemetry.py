"""Prometheus metrics for answered questions: outcome, tokens, cost, tool calls."""

from dataclasses import fields

from prometheus_client import Counter, Histogram

from tributary_agent.agent import Answer, Usage
from tributary_agent.tools import TOOLS

TOOL_NAMES = {t["name"] for t in TOOLS}

QUESTIONS = Counter("tributary_agent_questions_total",
                    "Questions by outcome: the answer's stop reason, or error", ["stop"])
TOKENS = Counter("tributary_agent_tokens_total", "Model tokens by kind", ["model", "kind"])
COST = Counter("tributary_agent_cost_usd_total", "Estimated model spend in USD", ["model"])
TOOL_CALLS = Counter("tributary_agent_tool_calls_total", "Query API tool calls by result",
                     ["tool", "result"])
ROUNDS = Histogram("tributary_agent_rounds", "Model calls per question",
                   buckets=(1, 2, 3, 4, 5, 6, 8, 10))

# Usage field → token kind label
KINDS = {"input_tokens": "input", "output_tokens": "output",
         "cache_read_input_tokens": "cache_read", "cache_creation_input_tokens": "cache_write"}


def record_answer(answer: Answer) -> None:
    QUESTIONS.labels(answer.stop).inc()
    ROUNDS.observe(answer.rounds)
    for f in fields(Usage):
        TOKENS.labels(answer.model, KINDS[f.name]).inc(getattr(answer.usage, f.name))
    if answer.cost_usd is not None:
        COST.labels(answer.model).inc(answer.cost_usd)
    for call in answer.tool_calls:
        tool = call.name if call.name in TOOL_NAMES else "unknown"
        TOOL_CALLS.labels(tool, "error" if call.is_error else "ok").inc()


def record_error() -> None:
    QUESTIONS.labels("error").inc()
