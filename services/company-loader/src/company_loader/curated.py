"""Hand-maintained inputs from src/company_loader/data/."""

import csv
from dataclasses import dataclass
from datetime import date
from importlib.resources import files


@dataclass(frozen=True)
class Succession:
    predecessor_cik: str
    successor_cik: str
    effective: date


@dataclass(frozen=True)
class TickerHistory:
    ticker: str
    cik: str
    valid_from: date | None
    valid_to: date | None


def _rows(name: str) -> list[dict[str, str]]:
    with files("company_loader").joinpath("data", name).open(newline="") as f:
        return list(csv.DictReader(f))


def _date(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


def tracked_tickers() -> list[str]:
    return [r["ticker"] for r in _rows("tracked.csv")]


def successions() -> list[Succession]:
    return [
        Succession(r["predecessor_cik"], r["successor_cik"], date.fromisoformat(r["effective"]))
        for r in _rows("successions.csv")
    ]


def ticker_history() -> list[TickerHistory]:
    return [
        TickerHistory(r["ticker"], r["cik"], _date(r["valid_from"]), _date(r["valid_to"]))
        for r in _rows("ticker_history.csv")
    ]
