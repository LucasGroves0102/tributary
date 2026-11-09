"""Insider filings (Forms 3, 4, 5; Riley #117): who filed, their role, and each line of the
filing's tables: transaction code, shares, price, value, shares owned after. Read from silver
(migration 054, parsed from the SEC's ownershipDocument XML), never guessed from text.

Rules (the web app only words them):
- A filing's `owners` are its reporting owners as filed; `roles` in this order: Director, the
  officer title (or "Officer"), "10% owner", the other text (or "Other").
- `code_label` is the SEC's Form 4 transaction code in words (General Instructions 8; CODES).
- `value` = shares x price, only for a transaction with both (a holding has none; a gift or an
  award at $0 is $0). Numbers are strings (str(Decimal)), like segments: exact as filed.
- `summary` per filing, for a filing index: the open-market purchases (P) and sales (S) and
  everything else, each as shares and value, so "sold 25,050 shares for $5.5M" needs no maths in
  the browser. Amendments (4/A) are listed as filed; nothing is netted against the original.
- Pages: newest first by (filed, accession_no), both descending. `next_cursor` (only with
  `has_more`) is the page's last pair; `cursor=` returns the filings after it, so a day's filings
  split across two pages (a grant day brings dozens of Form 4s for one company) are all listed.
  `before` (a day) stays for older clients: it skips the rest of that day by design.

GET /v1/companies/{id}/insiders: require(filings) first (filing content), then Store under RLS.
Per company and bounded (limit <= 100 filings): never catalog ids."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

# Form 4 transaction codes (SEC Form 4, General Instructions 8)
CODES = {
    "P": "Open-market purchase",
    "S": "Open-market sale",
    "V": "Reported early, voluntarily",
    "A": "Grant or award",
    "D": "Sold back to the company",
    "F": "Shares withheld for tax or exercise price",
    "I": "Discretionary transaction",
    "M": "Option exercise or conversion",
    "C": "Conversion of a derivative",
    "E": "Expiry of a short derivative",
    "H": "Expiry of a long derivative",
    "O": "Out-of-the-money derivative exercised",
    "X": "In-the-money derivative exercised",
    "G": "Gift",
    "L": "Small acquisition",
    "W": "Inherited",
    "Z": "Voting trust",
    "J": "Other",
    "K": "Equity swap",
    "U": "Tendered in a change of control",
}


def roles(owner: dict) -> list[str]:
    out = []
    if owner.get("director"):
        out.append("Director")
    if owner.get("officer"):
        out.append(owner.get("title") or "Officer")
    if owner.get("ten_percent"):
        out.append("10% owner")
    if owner.get("other"):
        out.append(owner.get("other_text") or "Other")
    return out


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def line_shape(r: dict) -> dict:
    value = (r["shares"] * r["price"] if r["kind"] == "transaction" and r["shares"] is not None
             and r["price"] is not None else None)
    return {
        "line": r["line"], "kind": r["kind"], "derivative": r["derivative"],
        "security_title": r["security_title"], "transaction_date": r["transaction_date"],
        "code": r["code"], "code_label": CODES.get(r["code"] or ""),
        "equity_swap": r["equity_swap"], "shares": _s(r["shares"]), "price": _s(r["price"]),
        "value": _s(value), "acquired_disposed": r["acquired_disposed"],
        "shares_after": _s(r["shares_after"]), "direct_indirect": r["direct_indirect"],
        "nature": r["nature"], "exercise_price": _s(r["exercise_price"]),
        "underlying_title": r["underlying_title"], "underlying_shares": _s(r["underlying_shares"]),
    }


def summary(lines: list[dict]) -> dict:
    """Shares and value per kind of transaction: bought (P), sold (S), other (acquired or
    disposed some other way: awards, withholding, gifts, exercises)."""
    out: dict[str, dict[str, Decimal]] = {}
    for r in lines:
        if r["kind"] != "transaction" or r["shares"] is None:
            continue
        key = "bought" if r["code"] == "P" else "sold" if r["code"] == "S" else "other"
        s = out.setdefault(key, {"shares": Decimal(0), "value": Decimal(0), "priced": True})
        s["shares"] += r["shares"]
        if r["price"] is None:
            s["priced"] = False
        else:
            s["value"] += r["shares"] * r["price"]
    return {k: {"shares": str(v["shares"]), "value": str(v["value"]) if v["priced"] else None}
            for k, v in out.items()}


CURSOR = r"^\d{4}-\d{2}-\d{2}_\d{10}-\d{2}-\d{6}$"


def cursor_of(f: dict) -> str:
    """A page's last filing as the next page's cursor: its (filed, accession_no), the order's
    key, so a day's remaining filings are never skipped (`before` alone would)."""
    return f"{f['filed'].isoformat()}_{f['accession_no']}"


def parse_cursor(cursor: str) -> tuple[date, str]:
    day, accession = cursor.split("_", 1)
    try:
        return date.fromisoformat(day), accession
    except ValueError:
        raise HTTPException(422, "cursor: not one this route gave") from None


def filing_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def shape(f: dict) -> dict:
    return {
        "accession_no": f["accession_no"], "form": f["form"], "filed": f["filed"],
        "period_of_report": f["period_of_report"], "aff10b5_one": f["aff10b5_one"],
        "owners": [{"cik": o.get("cik"), "name": o.get("name"), "roles": roles(o)}
                   for o in f["owners"]],
        "summary": summary(f["lines"]),
        "lines": [line_shape(r) for r in f["lines"]],
        "citation": {"accession_no": f["accession_no"], "form": f["form"], "filed": f["filed"],
                     "filing_url": filing_url(f["issuer_cik"], f["accession_no"])},
    }


def add_insider_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                       require: Callable[[str, str, str], None]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/insiders", response_model=dict)
    def insiders(
        company_id: CompanyId,
        user_id: User,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: Annotated[date | None, Query(description="filed before this date")] = None,
        cursor: Annotated[str | None, Query(
            pattern=CURSOR, description="the previous page's next_cursor: the filings after it")
        ] = None,
    ) -> dict[str, Any]:
        """A company's insider filings (Forms 3, 4, 5), newest first: owners and roles, each
        line's code, shares, price, value and shares owned after; `summary` per filing.
        `next_cursor` (when `has_more`) asks for the next page; `before` is a day, not a page
        boundary (a day can hold more filings than a page)."""
        after = parse_cursor(cursor) if cursor else None
        require(user_id, "filings", company_id)
        rows = store.insider_filings(user_id, company_id, limit + 1, before, after)
        page = rows[:limit]
        more = len(rows) > limit
        return {"company_id": company_id, "filings": [shape(f) for f in page],
                "has_more": more,
                "next_cursor": cursor_of(page[-1]) if more and page else None}
