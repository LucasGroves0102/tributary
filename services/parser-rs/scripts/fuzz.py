"""Differential test: random malformed HTML through both parsers, outputs must be identical.

Usage: python scripts/fuzz.py [--n 20000] [--seed 1] [--tokens 60] [--rust CMD]

Use --tokens 2000 for documents larger than libxml2's 4000-byte read size.

Each document is parsed as an exhibit (the whole html_to_text) and as a 10-K, 10-Q and 8-K.
Failing inputs are written to fuzz-failures/ for replay.
"""

import argparse
import json
import random
import shlex
import tempfile
from collections import Counter
from pathlib import Path

from bench import HERE, python_parse, rust_lines

TAGS = ["html", "head", "body", "title", "p", "div", "span", "td", "th", "tr", "table", "b",
        "font", "a", "li", "ul", "h1", "h3", "script", "style", "textarea", "xmp", "iframe",
        "noscript", "br", "hr", "img", "meta", "header", "ix:header", "section", "center", "form",
        "dd", "dt", "dl", "select", "option", "frameset", "noframes", "link", "base", "caption",
        "col", "thead", "tbody", "pre", "address", "blockquote", "P", "DIV", "TD", "x}p", "i",
        "plaintext", "noembed", "Body", "HEAD", "Title", "frame", "embed", "ol", "dir"]
ATTRS = ["", " style='display:none'", ' style="display: none"', " style=DISPLAY:NONE",
         " style", " class=x", " style='color:red' style='display:none'",
         ' http-equiv="Content-Type" content="text/html; charset=windows-1252"',
         ' charset="utf-8"', " charset=iso-8859-1", " a=&amp;b", " / ", " x='a>b'", " =y",
         ' style="display&#58;none"', " STYLE='x;display:none;y'", " style=display:none/"]
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
        "y" * 3000]
BYTES = [b"\xff", b"\x80", b"\xe2\x80", b"\xc3\xa9"]
RARE = [b"\x0c", b"\x01", b"&#12;", b"&#xFFFE;", b"\xef\xbf\xbe"]  # lxml rejects them in tails
PREFIXES = [b"", b"<?xml version='1.0' encoding='ASCII'?>", b"<html>", b"<!DOCTYPE html>",
            b"\xef\xbb\xbf<html>", b"  \n<HTML><HEAD><TITLE>t</TITLE></HEAD>",
            b"<DOCUMENT>\n<TYPE>EX-99.1\n<TEXT>\n", b"<!-- lead -->\n<html><body>",
            b"<?xml version='1.0'?><html><body><div>"]


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
                for kind in ("EX-99.1", "10-K", "10-Q", "8-K"):
                    e = {"id": f"{i}/{kind}", "path": str(path)}
                    e |= {"exhibit_type": kind} if kind.startswith("EX") else {"form": kind}
                    entries.append(e)
                    f.write(json.dumps(e) + "\n")
        bad = 0
        outcomes: Counter[str] = Counter()
        for e, r in zip(entries, rust_lines(shlex.split(args.rust), manifest, False), strict=True):
            raw = Path(e["path"]).read_bytes()
            py = python_parse(e, raw)
            rs = r.get("sections", f"error: {r.get('error')}")
            outcomes[py if isinstance(py, str) else "sections" if py else "no sections"] += 1
            if isinstance(py, str) and isinstance(rs, str) or py == rs:
                continue
            bad += 1
            failures.mkdir(exist_ok=True)
            (failures / Path(e["path"]).name).write_bytes(raw)
            if bad <= 5:
                print(f"DIFF {e['id']}: {raw[:300]!r}\n  python {py}\n  rust   {rs}")
    print(f"{args.n} documents x 4 parses: {bad} differences; python outcomes {dict(outcomes)}")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
