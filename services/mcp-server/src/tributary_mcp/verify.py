"""verify_figures: the agent's figure checker (tributary_agent.verify, imported unchanged) over
evidence this server reads as the key's user.

The assistant checks its answer against the tool results of its own conversation. A connected
model's conversation isn't here (each MCP request stands alone), so the evidence is read afresh:
for each company named, its /financials in the quarter and TTM views over the last `quarters`
quarters (the JSON the agent's get_financials passes to verify.py, without `readings`, which
verify.py doesn't read) and its annual per-share figures; and for each section named, its text
(up to 20,000 characters, the agent's get_section cap), whose tables and prose let a figure be
`quoted`. The accession numbers those answers mention are the ones a draft may cite.

verify.py's rules (its docstring) decide everything: verified, derived, quoted, unverified, and
an accession number nothing read cites. Its `ok` also asks an answer to say how fresh its data is,
a rule of the assistant's answers; here `all_verified` says only that no figure and no accession
number is unverified. Sources gain the cited filing's form and filed date, from the same answers.

The image installs the agent package without its dependencies (verify.py needs the standard
library only; tests/test_verify_tool.py checks that it still does)."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tributary_agent.verify import ACCESSION, verify

SECTION_CHARS = 20_000

SECTION_KEY = r"^(?:(?:[IV]+-)?[0-9]{1,2}[A-C]?(?:\.[0-9]{2})?|EX-99\.[0-9]{1,2})$"

RULES = (
    "verified: equals a figure read at the precision written; derived: the sum or difference of "
    "two comparable figures of one metric (adjacent quarters, a year apart, or two companies' "
    "periods within 45 days); quoted: appears in a section's text; unverified: none of these. "
    "Figures are dollar amounts as written ($96.2B, $1,234 million, 50.3 billion).")


class SectionRef(BaseModel):
    """A filing section whose text the draft quotes."""
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(pattern=r"^\d{10}$")
    section_key: str = Field(pattern=SECTION_KEY,
                             description='e.g. "7" (10-K MD&A), "I-2", "EX-99.1"')
    accession_no: str | None = Field(None, pattern=r"^\d{10}-\d{2}-\d{6}$")
    form: Literal["10-K", "10-Q", "8-K"] | None = None


def verify_reads(company_ids: list[str], metrics: list[str] | None, quarters: int,
                 sections: list[SectionRef]) -> list[tuple[str, dict[str, Any]]]:
    """The Query API reads (path, params) that make the evidence, in order; paths only from
    validated ids and keys."""
    reads: list[tuple[str, dict[str, Any]]] = []
    for cid in dict.fromkeys(company_ids):
        for view in ("quarter", "ttm"):
            reads.append((f"/v1/companies/{cid}/financials",
                          {"metrics": metrics, "quarters": quarters, "view": view}))
        reads.append((f"/v1/companies/{cid}/annual-per-share",
                      {"years": max(1, (quarters + 3) // 4)}))
    for s in sections:
        reads.append((f"/v1/companies/{s.company_id}/sections/{s.section_key}",
                       {"form": s.form, "accession_no": s.accession_no, "limit": 1,
                        "max_chars": SECTION_CHARS}))
    return reads


def _filings(evidence: list[Any]) -> dict[str, dict]:
    """accession number -> its form and filed date, from the citations in the evidence."""
    out: dict[str, dict] = {}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            acc = node.get("accession_no")
            if isinstance(acc, str) and "form" in node:
                out.setdefault(acc, {"form": node.get("form"), "filed": node.get("filed")})
            for v in node.values():
                if isinstance(v, dict | list):
                    walk(v)

    walk(evidence)
    return out


def _cited(source: Any, filings: dict[str, dict]) -> Any:
    """A figure's source with its filing's form and filed date beside the accession number."""
    if isinstance(source, list):
        return [_cited(s, filings) for s in source]
    if not isinstance(source, dict):
        return source
    out = {k: _cited(v, filings) for k, v in source.items()}
    acc = out.get("accession_no")
    if isinstance(acc, str) and acc in filings:
        out = {**out, **{k: v for k, v in filings[acc].items() if v is not None}}
    return out


def verify_against(text: str, evidence: list[Any]) -> dict:
    """verify.py's verdict on `text` over these Query API answers."""
    results = [json.dumps(e, separators=(",", ":"), default=str) for e in evidence]
    cited = {a for r in results for a in ACCESSION.findall(r)}
    v = verify(text, results, cited)
    filings = _filings(evidence)
    figures = [{"text": f.text, "value": f.value, "status": f.status,
                **({"source": _cited(f.source, filings)} if f.source else {})}
               for f in v.figures]
    counts = {s: sum(1 for f in v.figures if f.status == s)
              for s in ("verified", "derived", "quoted", "unverified")}
    return {
        "all_verified": counts["unverified"] == 0 and not v.unverified_accessions,
        "counts": counts,
        "figures": figures,
        "unverified_accessions": v.unverified_accessions,
        "dollar_figures_found": len(v.figures),
        "rules": RULES,
        **({"note": "No dollar figures found in the text (only amounts like $96.2B, $1,234 "
                    "million or 50.3 billion are checked)."} if not v.figures else {}),
    }

