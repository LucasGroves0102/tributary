"""Reports (app migration 005): a workspace item of kind `report`, the research note the
assistant (services/agent `report.py`) writes from a workspace's saved items.

The state is validated here as strictly as any other item's, and more: only the agent writes
it (through the report routes, which nginx keeps off the public site), but the Query API still
trusts nothing it is sent:

- `markdown`: at most MAX_MARKDOWN characters, no control characters but newlines and tabs.
  Embeds are lines `[[embed:item-N]]` and must name one of the report's `items`; every item is
  embedded at least once.
- `items`: up to MAX_REPORT_ITEMS snapshots of saved items (ref, item id, kind, name, state),
  each state validated against its kind exactly like a saved one. The web app renders an
  embedded chart from this state, reading its data through the Query API as the viewer.
- `citations`: filings the agent's tools returned (accession, form, filed, company, EDGAR URL,
  section keys); URLs must be https://www.sec.gov/.
- `verification`: the figure check (services/agent `verify.py`), with `ok` recomputed here so
  a report can't claim to be verified while listing an unverified figure.
- `as_of`, `written_at`, `model`, `questions` (what it counted against the assistant limits),
  `stop`, `workspace_id`.

A report grants nothing: it holds text the agent wrote from data the user could read, and the
embedded items' company ids and settings."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from query_api.workspaces import CIK, ISO_DATE, ITEM_NAME, StateError, clean_name, validate_state

MAX_MARKDOWN = 40_000
MAX_REPORT_ITEMS = 8
MAX_CITATIONS = 200
MAX_FIGURES = 400
MAX_REPORT_BYTES = 120_000  # the state as compact JSON (the table allows 131072 for reports)
MAX_REPORT_BODY = 160_000  # request bodies of the report routes
EMBED = re.compile(r"^\[\[embed:(item-[1-9])\]\]$", re.MULTILINE)
REF = r"^item-[1-9]$"
UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ACCESSION = r"^\d{10}-\d{2}-\d{6}$"
SECTION_KEY = r"^(?:[IV]+-)?[0-9]{1,2}[A-C]?(?:\.[0-9]{2})?$|^EX-99\.[0-9]{1,2}$"
TIMESTAMP = r"^\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?$"
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportItem(_Strict):
    ref: str = Field(pattern=REF)
    item_id: str = Field(pattern=UUID)
    kind: Literal["chart", "comps", "screen"]
    name: str = Field(max_length=4 * ITEM_NAME)
    state: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_name(v, ITEM_NAME)

    @model_validator(mode="after")
    def _state(self) -> ReportItem:
        try:
            self.state = validate_state(self.kind, self.state)
        except StateError as e:
            raise ValueError(f"{self.ref}: {e}") from None
        return self


class ReportCitation(_Strict):
    accession_no: str = Field(pattern=ACCESSION)
    form: str | None = Field(default=None, max_length=20)
    filed: str | None = Field(default=None, pattern=ISO_DATE)
    company_id: str | None = Field(default=None, pattern=CIK)
    url: str | None = Field(default=None, max_length=300, pattern=r"^https://www\.sec\.gov/")
    sections: list[Annotated[str, Field(pattern=SECTION_KEY)]] = Field(default=[], max_length=30)


class ReportFigure(_Strict):
    text: str = Field(max_length=60)
    status: Literal["verified", "derived", "quoted", "unverified"]


class ReportVerification(_Strict):
    ok: bool = False
    figures: list[ReportFigure] = Field(default=[], max_length=MAX_FIGURES)
    unverified_accessions: list[Annotated[str, Field(pattern=ACCESSION)]] = Field(
        default=[], max_length=50)
    used_data: bool = False
    freshness: bool = False

    @model_validator(mode="after")
    def _ok(self) -> ReportVerification:
        """Recomputed, never taken from the request (verify.Verification.ok)."""
        self.ok = (not self.unverified_accessions
                   and all(f.status != "unverified" for f in self.figures)
                   and (self.freshness or not self.used_data))
        return self


class ReportState(_Strict):
    workspace_id: str = Field(pattern=UUID)
    markdown: str = Field(min_length=1, max_length=MAX_MARKDOWN)
    items: list[ReportItem] = Field(min_length=1, max_length=MAX_REPORT_ITEMS)
    citations: list[ReportCitation] = Field(default=[], max_length=MAX_CITATIONS)
    verification: ReportVerification
    as_of: str | None = Field(default=None, pattern=TIMESTAMP)
    written_at: str = Field(pattern=TIMESTAMP)
    model: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._:-]+$")
    questions: int = Field(ge=1, le=10)
    stop: Literal["end_turn", "max_rounds", "max_tokens", "refusal"]

    @field_validator("markdown")
    @classmethod
    def _text(cls, v: str) -> str:
        if _CONTROL.search(v):
            raise ValueError("markdown contains control characters")
        return v

    @model_validator(mode="after")
    def _embeds(self) -> ReportState:
        refs = [i.ref for i in self.items]
        if len(set(refs)) != len(refs) or len({i.item_id for i in self.items}) != len(refs):
            raise ValueError("items repeat")
        embedded = set(EMBED.findall(self.markdown))
        if unknown := embedded - set(refs):
            raise ValueError(f"markdown embeds unknown items: {', '.join(sorted(unknown))}")
        if missing := set(refs) - embedded:
            raise ValueError(f"items never embedded: {', '.join(sorted(missing))}")
        return self


def validate_report(raw: Any) -> dict:
    """A report's state as stored; StateError (with a safe message) if it doesn't fit."""
    try:
        state = ReportState.model_validate(raw).model_dump(exclude_none=True)
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        raise StateError(str(e)) from None
    if len(json.dumps(state, separators=(",", ":")).encode()) > MAX_REPORT_BYTES:
        raise StateError(f"report is larger than {MAX_REPORT_BYTES} bytes")
    return state
