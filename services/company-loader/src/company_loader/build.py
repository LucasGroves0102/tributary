"""Turn SEC reference data + curated files into company identity rows. Pure, no I/O."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from company_loader.curated import Succession, TickerHistory


@dataclass(frozen=True)
class TickerRow:
    cik: str
    ticker: str
    name: str


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    current_cik: str
    sic: str | None
    sic_description: str | None
    entity_type: str | None
    tracked: bool


@dataclass(frozen=True)
class CikRow:
    cik: str
    company_id: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class Alias:
    """company_id is filled in from company_ciks at load time."""

    cik: str
    alias_type: str  # ticker | name
    alias: str
    valid_from: date | None
    valid_to: date | None
    source: str  # sec_submissions | curated


@dataclass
class Snapshot:
    companies: list[Company] = field(default_factory=list)
    ciks: list[CikRow] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)  # replaced wholesale each run
    current_tickers: set[tuple[str, str, str]] = field(default_factory=set)  # (type, alias, cik)


def parse_tickers(raw: dict) -> list[TickerRow]:
    """company_tickers.json is {"0": {"cik_str": 320193, "ticker": "AAPL", "title": ...}, ...}"""
    return [TickerRow(str(r["cik_str"]).zfill(10), r["ticker"], r["title"]) for r in raw.values()]


class Lineage:
    """Maps each CIK to its company_id: the first CIK in its chain of successions."""

    def __init__(self, successions: Iterable[Succession]) -> None:
        self._predecessor = {s.successor_cik: s for s in successions}
        self._successor = {s.predecessor_cik: s for s in self._predecessor.values()}

    def root(self, cik: str) -> str:
        while cik in self._predecessor:
            cik = self._predecessor[cik].predecessor_cik
        return cik

    def members(self, cik: str) -> list[str]:
        """Every CIK in this CIK's chain, oldest first."""
        chain = [self.root(cik)]
        while chain[-1] in self._successor:
            chain.append(self._successor[chain[-1]].successor_cik)
        return chain

    def valid_range(self, cik: str) -> tuple[date | None, date | None]:
        start = self._predecessor.get(cik)
        end = self._successor.get(cik)
        return (start.effective if start else None, end.effective if end else None)


def tracked_ciks(
    tickers: list[TickerRow], tracked: Iterable[str], lineage: Lineage
) -> set[str]:
    """Every CIK in the lineage of each tracked ticker. Raises if a tracked ticker is unknown."""
    by_ticker = {t.ticker: t.cik for t in tickers}
    missing = [t for t in tracked if t not in by_ticker]
    if missing:
        raise ValueError(f"tracked tickers not in company_tickers.json: {missing}")
    return {m for t in tracked for m in lineage.members(by_ticker[t])}


def build(
    tickers: list[TickerRow],
    submissions: dict[str, dict],
    successions: list[Succession],
    history: list[TickerHistory],
    tracked: list[str],
) -> Snapshot:
    lineage = Lineage(successions)
    tracked_roots = {lineage.root(c) for c in tracked_ciks(tickers, tracked, lineage)}
    name_by_cik = {t.cik: t.name for t in tickers}

    all_ciks = (
        set(name_by_cik)
        | {c for s in successions for c in (s.predecessor_cik, s.successor_cik)}
        | {h.cik for h in history}
    )
    by_root: dict[str, list[str]] = defaultdict(list)
    for cik in all_ciks:
        by_root[lineage.root(cik)].append(cik)

    snap = Snapshot()
    for root in sorted(by_root):
        current = lineage.members(root)[-1]
        sub = submissions.get(current, {})
        snap.companies.append(
            Company(
                company_id=root,
                name=name_by_cik.get(current) or sub.get("name") or current,
                current_cik=current,
                sic=sub.get("sic") or None,
                sic_description=sub.get("sicDescription") or None,
                entity_type=sub.get("entityType") or None,
                tracked=root in tracked_roots,
            )
        )
        for cik in by_root[root]:
            snap.ciks.append(CikRow(cik, root, *lineage.valid_range(cik)))

    for cik, sub in submissions.items():
        start, end = lineage.valid_range(cik)
        snap.aliases.append(Alias(cik, "name", sub["name"], start, end, "sec_submissions"))
        for former in sub.get("formerNames", []):
            snap.aliases.append(
                Alias(
                    cik, "name", former["name"],
                    _iso_date(former.get("from")), _iso_date(former.get("to")),
                    "sec_submissions",
                )
            )
    for h in history:
        snap.aliases.append(Alias(h.cik, "ticker", h.ticker, h.valid_from, h.valid_to, "curated"))

    for t in tickers:
        snap.current_tickers.add(("ticker", t.ticker, t.cik))
        snap.current_tickers.add(("name", t.name, t.cik))
    return snap


def _iso_date(s: str | None) -> date | None:
    """SEC timestamps look like 2005-05-06T04:00:00.000Z; keep the date."""
    return date.fromisoformat(s[:10]) if s else None
