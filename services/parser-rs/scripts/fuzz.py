"""Differential test: random malformed HTML through both parsers, outputs must be identical.

Usage: python scripts/fuzz.py [--n 20000] [--seed 1] [--tokens 60] [--rust CMD]

Use --tokens 2000 for documents larger than libxml2's 4000-byte read size.

Each document is parsed as an exhibit (the whole html_to_text) and as a 10-K, 10-Q, 8-K, 20-F
(rule 12) and 6-K and 40-F/A (rule 13); sections are compared with their rule 11 tables, and a
primary document's printed facts (rule 14).
Failing inputs are written to fuzz-failures/ for replay.
"""

import argparse
import json
import random
import shlex
import tempfile
from collections import Counter
from pathlib import Path

from bench import HERE, python_parse, rust_lines, rust_result

TAGS = ["html", "head", "body", "title", "p", "div", "span", "td", "th", "tr", "table", "b",
        "font", "a", "li", "ul", "h1", "h3", "script", "style", "textarea", "xmp", "iframe",
        "noscript", "br", "hr", "img", "meta", "header", "ix:header", "section", "center", "form",
        "dd", "dt", "dl", "select", "option", "frameset", "noframes", "link", "base", "caption",
        "col", "thead", "tbody", "pre", "address", "blockquote", "P", "DIV", "TD", "x}p", "i",
        "plaintext", "noembed", "Body", "HEAD", "Title", "frame", "embed", "ol", "dir",
        # rule 11: tables, rows, cells and head rows, more often
        "table", "tr", "td", "td", "th", "thead", "TR", "x}td", "tfoot",
        # rule 14: inline XBRL facts, contexts and units
        "ix:nonFraction", "ix:nonfraction", "IX:NONFRACTION", "ix2:nonFraction", "xbrli:context",
        "xbrli:unit", "xbrli:measure", "xbrli:divide", "xbrli:startDate", "xbrli:endDate",
        "xbrli:instant", "xbrldi:explicitMember", "xbrldi:typedMember", "nonfraction",
        "x}ix:nonfraction", "ix:hidden"]
ATTRS = ["", " style='display:none'", ' style="display: none"', " style=DISPLAY:NONE",
         " style", " class=x", " style='color:red' style='display:none'",
         ' http-equiv="Content-Type" content="text/html; charset=windows-1252"',
         ' charset="utf-8"', " charset=iso-8859-1", " a=&amp;b", " / ", " x='a>b'", " =y",
         ' style="display&#58;none"', " STYLE='x;display:none;y'", " style=display:none/",
         # rule 11: colspans
         " colspan=2", " colspan='3'", " COLSPAN=0", " colspan=&#52;", " colspan=x",
         " colspan=' 12px'", " colspan=99999999999999999999", " colspan", " colspan=2 colspan=5",
         # rule 14: ids, names, references and the attributes kept as written
         ' id="c1"', " id=c2", " ID='u1'", ' id="c1" id="c2"', " id", ' id=""',
         ' name="us-gaap:Revenues" contextRef="c1" unitRef="u1"', " name=x:y contextref=c2",
         ' name="" contextRef="c1"', ' NAME="a:B" CONTEXTREF="c1" unitref=u2', " name=a:B",
         ' name="a:C" contextRef="c2" scale="6" sign="-" decimals="-6"'
         ' format="ixt:num-dot-decimal"',
         ' name="a:D" contextRef="c1" scale decimals=INF', ' contextRef="c1" contextref="c2"',
         ' name="a:&amp;E" contextRef="c&#49;"', " style='display:none' name=a:F contextRef=c1"]
TEXT = ["Item 7.", "Item 1A", "ITEM 2.02 Results", "PART II", "Part I", "Item 8. Financial",
        "Management’s discussion and analysis", "Management's Discussion and Analysis of x",
        "Report of Independent Registered Public Accounting Firm", "Item\n7", "Part\tIV.",
        "Revenue grew.", "x" * 60, "&amp;", "&amp", "&notin;", "&notit;", "&#65;", "&#x41",
        "&#150;", "&#0;", "&#xD800;", "&#99999999;", "&;", "&#;", "&#x;", "&nbsp", "&copy=",
        "&AMP;", "&lt", "&#x7f;", " ", "  ", "\t", "\r\n", "\r", "\n",
        "\xa0", "​", " ", "　", "é", "’", "İtem 3", "\x00",
        "a < b", "<3", "< x", "</ >", "</3>", "<!x>", "<!-- c -->", "<!--->", "<!-->",
        "<!-- a --!>", "<?pi?>", "<!DOCTYPE x>", "</p>", "</body>", "</html>", "</td>",
        "<p/>", "<div/>", "<br/>", "</table>", "</div>",
        "<script><!-- <script> </script> --> x</script>", "<!--<script>", "Item \u0663.",
        "Part\nII", "Item 1.\nItem 1.\n", "Item 2.\nRisk\n", "Item 7. MD&A\n" * 2,
        "Management’s discussion and analysis " + "y" * 100, "\u212aey", "ıtem 4", "ſ",
        "<ix:header>Item 9</ix:header>", "<title>Item 5</title>", "\x85", "\ufeff",
        "http-equiv content charset=koi8-r ", "<!-- HTTP-EQUIV CONTENT CHARSET=iso-8859-2 -->",
        "y" * 3000,
        # rule 9: item titles without numbers, their core (punctuation, parenthetical) and fold
        "\nRISK FACTORS\n", "\nLegal Proceedings.\n", "\nExhibits:\n", "\nBusiness\n",
        "\nControls and Procedures (Unaudited)\n", "\nOther Information ()\n",
        "\nMANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF"
        " OPERATIONS (“MD&A”)\n", "\nPart I, Item 2\n", "\nPART II—OTHER INFORMATION\n",
        "\nRİSK FACTORS\n", "\nFORM 10-K SUMMARY\n", "\nUnregistered Sales of Equity Securities"
        " and Use of Proceeds (a list longer than twenty)\n", "\nExhibits (a))\n", "Risk Factors",
        # rule 11: table fragments (rows, cells, spans, head rows, nesting, spacer cells)
        "<table><tr><td>Revenue</td><td>$</td><td>1,234</td></tr><tr><td> </td></tr>",
        "<tr><td>a</td><td colspan=2> b  c </td></tr>", "<tr><th>x</th><th></th></tr>",
        "<thead><tr><td></td><td colspan='3'>Three Months Ended</td></tr></thead>",
        "<td>\xa0</td>", "<td>$\xa0 1,100 </td>", "<td><p>Net</p><p>sales</p></td>", "</td><td>",
        "</tr><tr>", "<table><tr><td>in<table><tr><td>ner</td></tr></table></td></tr></table>",
        # rule 12: 20-F items, sub-item headings and the 3.D risk factors line
        "\nItem 3. Key Information\n", "\nD. Risk Factors\n", "\n3.D RISK FACTORS\n", "Item 3.D",
        "\nItem 3.D. Risk factors\n", "Item 5.A", "ITEM 4.B.", "Item 3.Dividends", "Item 4A",
        "Item 5A",
        "Item 16F", "\nItem 18. Financial Statements\n", "\n3.D\u2014Risk Factors (continued)\n",
        "\nİtem 3.d - Rİsk factors\n", "\nD Risk Factors.\n", "Item 2.02.A", "\ndRisk Factors\n",
        # rule 14: a header's contexts and units, and facts (whole, so many land in the text)
        '<div style="display:none"><ix:header><xbrli:context id="c1"><xbrli:period>'
        "<xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate> 2026-03-31 </xbrli:endDate>"
        '</xbrli:period></xbrli:context><xbrli:context id="c2"><xbrli:period><xbrli:instant>'
        '2026-03-31</xbrli:instant></xbrli:period></xbrli:context><xbrli:unit id="u1">'
        '<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit><xbrli:unit id="u2"><xbrli:divide>'
        "<xbrli:measure>iso4217:EUR</xbrli:measure><xbrli:measure>xbrli:shares</xbrli:measure>"
        "</xbrli:divide></xbrli:unit></ix:header></div>",
        '<xbrli:context id="c3"><xbrli:segment><xbrldi:explicitMember>m</xbrldi:explicitMember>'
        "</xbrli:segment><xbrli:instant>2026-03-31</xbrli:instant></xbrli:context>",
        '<xbrli:context id="c1"><xbrli:instant></xbrli:instant><xbrli:enddate>x</xbrli:enddate>'
        "</xbrli:context>", '<xbrli:unit id="u1"><xbrli:measure>a:b:c</xbrli:measure></xbrli:unit>',
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="c1" unitRef="u1" scale="6">1,234'
        "</ix:nonFraction>", '<ix:nonFraction name="us-gaap:Cash" contextRef="c2" unitRef="u2"'
        ' sign="-">5<ix:nonFraction name="a:N" contextRef="c2"> 6 </ix:nonFraction>'
        "</ix:nonFraction>",
        '<td><ix:nonFraction name="a:T" contextRef="c1">\xa0 7\n</ix:nonFraction></td>',
        '<ix:nonFraction name="a:Z" contextRef="c3">8</ix:nonFraction>',
        '<ix:nonFraction name="a:E" contextRef="c1"></ix:nonFraction>', "\n12\nPage\n",
        # rule 15: a 10-Q's contents listing items 1 and 2 with entries, and a gap holding them
        "\nPart I\nItem 1.\nFinancial Statements\nConsolidated balance sheets as of June 30\n3\n"
        "Item 2.\nManagement's Discussion\nConsolidated Financial Highlights\n4\nItem 3. Risk\n"
        "Part II\nItem 1. Legal\nItem 3. Defaults\n",
        "\nCONSOLIDATED BALANCE SHEETS\n", "\nConsolidated financial highlights (unaudited)\n",
        "\nItem 3. Quantitative\n", "\nPart II\nItem 1. Legal Proceedings\n", "\nOperations\n"]
BYTES = [b"\xff", b"\x80", b"\xe2\x80", b"\xc3\xa9"]
RARE = [b"\x0c", b"\x01", b"&#12;", b"&#xFFFE;", b"\xef\xbf\xbe"]  # lxml rejects them in tails
PREFIXES = [b"", b"<?xml version='1.0' encoding='ASCII'?>", b"<html>", b"<!DOCTYPE html>",
            b"\xef\xbb\xbf<html>", b"  \n<HTML><HEAD><TITLE>t</TITLE></HEAD>",
            b"<DOCUMENT>\n<TYPE>EX-99.1\n<TEXT>\n", b"<!-- lead -->\n<html><body>",
            b"<?xml version='1.0'?><html><body><div>",
            # rule 14: a header first, so the facts that follow have contexts and units
            b'<html><body><div style="display:none"><ix:header><xbrli:context id="c1"><x:period>'
            b"<xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate>"
            b'</x:period></xbrli:context><xbrli:context id="c2"><xbrli:instant>2026-03-31'
            b'</xbrli:instant></xbrli:context><xbrli:unit id="u1"><xbrli:measure>iso4217:USD'
            b"</xbrli:measure></xbrli:unit></ix:header></div>"]
PREFIXES += PREFIXES[-1:] * 3  # often


def document(rng: random.Random, tokens: int) -> bytes:
    out = [rng.choice(PREFIXES)]
    for _ in range(rng.randint(1, tokens)):
        r = rng.random()
        if r < 0.35:
            out.append(f"<{rng.choice(TAGS)}{rng.choice(ATTRS)}>".encode())
        elif r < 0.55:
            out.append(f"</{rng.choice(TAGS)}>".encode())
        elif r < 0.95:
            out.append(rng.choice(TEXT).encode())
        elif r < 0.995:
            out.append(rng.choice(BYTES))
        else:
            out.append(rng.choice(RARE))
    if rng.random() < 0.02:
        out.append(b"<span>a" * rng.randint(250, 260))
    if rng.random() < 0.3:
        out.append(rng.choice([b"<", b"</", b"<p", b"<!--", b"&", b"&#", b"<a href='x"]))
    return b"".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tokens", type=int, default=60, help="at most this many per document")
    ap.add_argument("--rust", default=str(HERE / "target/release/parser-rs"))
    args = ap.parse_args()
    rng = random.Random(args.seed)
    failures = Path("fuzz-failures")
    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "manifest.jsonl"
        entries = []
        with manifest.open("w") as f:
            for i in range(args.n):
                path = Path(tmp) / f"{i}.htm"
                path.write_bytes(document(rng, args.tokens))
                for kind in ("EX-99.1", "10-K", "10-Q", "8-K", "20-F", "6-K", "40-F/A"):
                    e = {"id": f"{i}/{kind}", "path": str(path)}
                    e |= {"exhibit_type": kind} if kind.startswith("EX") else {"form": kind}
                    entries.append(e)
                    f.write(json.dumps(e) + "\n")
        bad = 0
        outcomes: Counter[str] = Counter()
        for e, r in zip(entries, rust_lines(shlex.split(args.rust), manifest, False), strict=True):
            raw = Path(e["path"]).read_bytes()
            py = python_parse(e, raw)
            rs = rust_result(r)
            outcomes[py if isinstance(py, str) else "sections" if py["sections"]
                     else "no sections"] += 1
            if isinstance(py, str) and isinstance(rs, str) or py == rs:
                continue
            bad += 1
            failures.mkdir(exist_ok=True)
            (failures / Path(e["path"]).name).write_bytes(raw)
            if bad <= 5:
                print(f"DIFF {e['id']}: {raw[:300]!r}\n  python {py}\n  rust   {rs}")
    print(f"{args.n} documents x 7 parses: {bad} differences; python outcomes {dict(outcomes)}")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
