"""parser-peers: peers by business, from what each company's 10-K says it does (migration 047).

Spec (the Query API's peer ranking reads what this writes; query_api/peers.py has the rest):

1. Documents. For every company with gold financials, Item 1 (Business) of its latest 10-K filed
   in the last MAX_AGE_DAYS that has a usable one, the first TEXT_CHARS characters (the overview,
   products, markets and competition come first; the long tail is regulation and human capital).
   Usable: at least MIN_CHARS long (Intel's, GE's and McDonald's Item 1 is a cross-reference,
   "Item 1. Business: Pages 3-5") and not titled financial statements (a mis-parse: Microchip's held its
   balance sheet). 74 of 4,983 latest 10-Ks failed this on 2026-11-09.
2. Terms. Lowercased letters-only words of 3+ letters, cut into runs at anything else (digits,
   punctuation, line breaks), each word made a simple singular (`normal`). Stop words (English,
   filing furniture, places: being based in Ohio says nothing about what a company does) end a
   run. A term is a word, or two adjacent words of one run ("data center", "athletic footwear").
3. Weights. Document frequency df over all documents (pass 1). A company's vector keeps terms with
   MIN_DF <= df <= MAX_DF_SHARE of documents: rarer ones are brand names and one-off phrasing that
   can match almost nobody, commoner ones say nothing (Hoberg and Phillips' text-based industries
   drop words in over 25% of 10-Ks too). It also drops its own name's words and tickers, and
   STRUCTURE phrases (share classes, registration rights, SPAC mergers: corporate history, not a
   business). Weight = (1 + ln tf) x ln(N / df); the TOP_TERMS heaviest kept, L2-normalised.
4. Similarity. Cosine of two vectors (a dot product: both have length 1). Each company keeps its
   NEIGHBOURS most similar companies, with the WHY_TERMS shared terms that add the most
   (weight here x weight there), so the API can say what made them similar.
5. Competitors named. Sentences of Item 1 that mention competition (`compet`) are searched for
   other companies' names (`Names`: SEC names and former names, normalised like
   silver.normalize_name; short or common single words never count). Each hit is stored with the
   sentence's offsets in the document's text (like silver.filing_sections' char_start), to cite.

Everything is replaced in one transaction per run, so readers see the old or the new answer.
Cost, measured on hermes' Item 1 texts of 2026-11-09 loaded into a local Postgres (the read alone
took 6 s on hermes): 4,888 usable documents, 79 s wall (pass 1 20 s, pass 2 31 s, similarities
26 s, writing 2 s), peak 406 MB (pass 1's document frequencies, released before pass 2); it
writes 4,888 profiles, 293,280 peer rows and 4,166 mentions, 60 MB with indexes. See
services/parser/README.md "Peers by business".
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import time
from array import array
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

ALGORITHM = "business-1"  # bump when the spec above changes; stored with each profile
MAX_AGE_DAYS = 1100
MIN_CHARS = 3_000
TEXT_CHARS = 80_000
MIN_DF = 20
MAX_DF_SHARE = 0.25
TOP_TERMS = 400
NEIGHBOURS = 60
WHY_TERMS = 5
PROFILE_TERMS = 15
FETCH_BATCH = 200
LOCK = 2_047_001  # pg_advisory_lock key: one run at a time

log = logging.getLogger("parser_peers")

_ENGLISH = """
a about above after again against all also although am among an and any are as at be because been
before being below between both but by can could did do does doing down during each either else
etc even ever every few for from further had has have having he her here hers herself him himself
his how however i if in into is it its itself just less like may me might more most much must my
myself neither no nor not now of off on once only or other others otherwise our ours ourselves out
over own per rather same shall she should since so some such than that the their theirs them
themselves then there therefore these they this those though through thus to too under until up
upon us very via was we were what whatever when whenever where whereas whether which while who
whom whose why will with within without would yet you your yours yourself yourselves
one two three four five six seven eight nine ten first second third fourth fifth
january february march april june july august september october november december
"""
_FILING = """
item items part form annual report reports company companies corporation inc incorporated corp
llc ltd plc fiscal year years quarter quarters million millions billion billions thousand percent
approximately including include includes included primarily generally certain various related
based new use used using substantially significant significantly respectively refer reference
see note notes herein thereof therein hereof whereby page pages table
"""
_PLACES = """
alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii
idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan
minnesota mississippi missouri montana nebraska nevada hampshire jersey mexico york carolina
dakota ohio oklahoma oregon pennsylvania rhode tennessee texas utah vermont virginia washington
wisconsin wyoming puerto rico guam columbia
america americas american north south east west northern southern eastern western central
midwest midwestern northeast southeast southwest northwest pacific atlantic gulf coast
canada canadian mexican brazil brazilian argentina chile peru colombia europe european
emea apac asia asian africa african australia australian zealand china chinese hong kong taiwan
japan japanese korea korean india indian singapore malaysia thailand vietnam indonesia
philippines israel israeli germany german france french italy italian spain spanish portugal
netherlands dutch belgium switzerland swiss austria sweden norway denmark finland ireland irish
kingdom britain british england london scotland poland russia russian ukraine turkey
saudi arabia emirates uae dubai qatar egypt nigeria
chicago houston dallas atlanta boston angeles francisco seattle denver miami phoenix detroit
philadelphia pittsburgh minneapolis cleveland charlotte nashville austin diego jose
"""
# Human capital: every 10-K since 2020 describes its people, and those words made "performance
# culture" and "rotational program" the reason Vertiv resembled Johnson Controls.
_PEOPLE = """
culture talent talented workforce diversity diverse inclusion inclusive belonging wellbeing bachelor
rotational internship internships intern mentoring mentorship volunteer volunteering philanthropy
philanthropic scholarship upskilling reskilling headcount
"""
STOP = frozenset((_ENGLISH + _FILING + _PLACES + _PEOPLE).split())

# Corporate structure, not a business: share classes, registration rights, SPAC mergers. A
# company's own history (Vertiv's 2020 merger with a SPAC) otherwise makes it look like every
# other company that went public that way. Terms as `terms` makes them (singular, lowercase).
STRUCTURE = frozenset("""
business combination|blank check|special purpose|purpose acquisition|acquisition corp|trust account
registration right|right agreement|stockholder agreement|shareholder agreement|restated registration
class common|common stock|share class|class share|preferred stock|warrant|public warrant
placement warrant|sponsor|sponsor member|founder share|shelf takedown|underwritten shelf
underwritten offering|rra
merger agreement|merger sub|closing date|lock up|earnout|earn out|pipe investor|pipe investment
""".replace("\n", "|").strip("|").split("|"))

_RUN = re.compile(r"[a-z]+(?:[ \t'\-]+[a-z]+)*")
_WORD = re.compile(r"[a-z]+")


def normal(word: str) -> str | None:
    """A lowercase word as a simple singular ('products' and 'product' count once); None drops
    words under 3 letters."""
    if len(word) < 3:
        return None
    if len(word) > 4:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("sses"):
            return word[:-2]
        if word.endswith("s") and not word.endswith(("ss", "us", "is", "ous")):
            return word[:-1]
    return word


def terms(text: str) -> Counter:
    """Term counts of a text: words and two-word phrases (spec 2)."""
    out: Counter = Counter()
    for run in _RUN.findall(text.lower()):
        prev = None
        for raw in _WORD.findall(run):
            w = normal(raw)
            if w is None or w in STOP:
                prev = None
                continue
            out[w] += 1
            if prev is not None:
                out[f"{prev} {w}"] += 1
            prev = w
    return out


def own_words(names: Iterable[str], tickers: Iterable[str]) -> frozenset[str]:
    """A company's own name words and tickers, as terms make them: never evidence of a peer."""
    words = {normal(w) for n in names for w in _WORD.findall(n.lower())}
    words |= {t.lower() for t in tickers}
    return frozenset(w for w in words if w and w not in STOP)


@dataclass
class Vocabulary:
    """Pass 1: document frequencies. `keep` (after `close`) maps a usable term to its id."""
    docs: int = 0
    df: Counter = field(default_factory=Counter)
    keep: dict[str, int] = field(default_factory=dict)
    idf: array = field(default_factory=lambda: array("d"))
    names: list[str] = field(default_factory=list)
    common: frozenset[str] = frozenset()  # single words in over COMMON_SHARE of documents

    def add(self, counts: Counter) -> None:
        self.docs += 1
        self.df.update(counts.keys())

    def close(self) -> None:
        top = MAX_DF_SHARE * self.docs
        for t, n in self.df.items():
            if MIN_DF <= n <= top and t not in STRUCTURE:
                self.keep[t] = len(self.names)
                self.names.append(t)
                self.idf.append(math.log(self.docs / n))
        self.common = frozenset(t for t, n in self.df.items()
                                if n > COMMON_SHARE * self.docs and " " not in t)
        self.df = Counter()  # pass 1's memory, released


def vector(counts: Counter, vocab: Vocabulary, own: frozenset[str],
           top: int = TOP_TERMS) -> dict[int, float]:
    """Spec 3: term id -> weight, the `top` heaviest, length 1."""
    w: dict[int, float] = {}
    for t, n in counts.items():
        i = vocab.keep.get(t)
        if i is None or any(p in own for p in t.split(" ")):
            continue
        w[i] = (1 + math.log(n)) * vocab.idf[i]
    best = sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    norm = math.sqrt(sum(x * x for _, x in best)) or 1.0
    return {i: x / norm for i, x in best}


@dataclass(frozen=True)
class Neighbour:
    company_id: str
    similarity: float
    terms: tuple[str, ...]


def neighbours(vectors: dict[str, dict[int, float]], vocab: Vocabulary,
               keep: int = NEIGHBOURS, why: int = WHY_TERMS) -> dict[str, list[Neighbour]]:
    """Spec 4 through an inverted index: each company's `keep` most similar others, best first
    (ties by company id), each with the `why` shared terms that add the most."""
    ids = sorted(vectors)
    index = {c: k for k, c in enumerate(ids)}
    posting_docs: dict[int, array] = {}
    posting_w: dict[int, array] = {}
    for c in ids:
        for t, x in vectors[c].items():
            posting_docs.setdefault(t, array("i")).append(index[c])
            posting_w.setdefault(t, array("d")).append(x)
    out: dict[str, list[Neighbour]] = {}
    acc = [0.0] * len(ids)
    for c in ids:
        me = index[c]
        touched: set[int] = set()
        for t, x in vectors[c].items():
            for d, y in zip(posting_docs[t], posting_w[t], strict=True):
                if d != me:
                    acc[d] += x * y
                    touched.add(d)
        best = sorted(touched, key=lambda d: (-acc[d], ids[d]))[:keep]
        mine = vectors[c]
        rows = []
        for d in best:
            theirs = vectors[ids[d]]
            shared = sorted(((mine[t] * theirs[t], t) for t in mine if t in theirs),
                            key=lambda p: (-p[0], p[1]))
            rows.append(Neighbour(ids[d], round(acc[d], 6),
                                  tuple(_distinct([vocab.names[t] for _, t in shared], why))))
        for d in touched:
            acc[d] = 0.0
        out[c] = rows
    return out


def _distinct(names: Sequence[str], n: int) -> list[str]:
    """The first n terms, skipping a word already inside a chosen phrase and a phrase made of
    chosen words ("cooled" after "liquid cooled"), so the reasons don't repeat themselves."""
    picked: list[str] = []
    for t in names:
        parts = set(t.split(" "))
        if any(t == p or (" " not in t and t in p.split(" ")) or
               (" " in t and parts <= set(" ".join(picked).split(" "))) for p in picked):
            continue
        picked.append(t)
        if len(picked) == n:
            break
    return picked


def profile_terms(vec: dict[int, float], vocab: Vocabulary, n: int = PROFILE_TERMS) -> list[str]:
    ranked = [vocab.names[t] for t, _ in sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))]
    return _distinct(ranked, n)


# Competitors named -------------------------------------------------------------------------

_SUFFIX = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|"
                     r"HOLDINGS|THE)\b")
# words a company's name may drop when others write it ("Johnson Controls", not "Johnson
# Controls International")
_OPTIONAL = ("INTERNATIONAL", "GROUP", "HOLDING", "GLOBAL", "WORLDWIDE", "ENTERPRISES", "USA",
             "US", "NV", "SA", "AG", "SE")
# a sentence ends at . ! ? or ; followed by space and a capital or an opening bracket/quote, and
# at every line break; "e.g., Delta Electronics, Inc., Stulz GmbH" stays one sentence
_SENTENCE_END = re.compile(r"(?<=[.!?;])[ \t]+(?=[A-Z\"(“])|\n")
_COMPETE = re.compile(r"compet", re.I)
COMMON_SHARE = 0.02  # a one-word name that is a word in over 2% of documents is an ordinary word
# Ordinary words that are also one-word company names, whatever their share
ORDINARY = frozenset("""
target gap visa square block ball crown match meta snap lumen focus vision insight motion progress
unity origin harmony genesis summit pinnacle premier pioneer frontier liberty heritage prime core
key fidelity principal charter evergreen catalyst
""".split())


def sentences(text: str) -> Iterator[tuple[int, int]]:
    """(start, end) of each sentence of a text, by _SENTENCE_END."""
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if m.start() > start:
            yield start, m.start()
        start = m.end()
    if start < len(text):
        yield start, len(text)


def normalize_name(name: str) -> str:
    """silver.normalize_name (migration 002), in Python: "Eaton Corporation plc" -> "EATON"."""
    s = name.upper().replace("&", " AND ")
    s = re.sub(r"/[A-Z]{2,3}/?$", "", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _SUFFIX.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"( AND)+\s*$", "", s)
    return s.strip()


class Names:
    """Company names to look for in a sentence. `common`: lowercase words too ordinary to be a
    name on their own (a single-word name counts only if it isn't one, is 4+ letters and is
    written capitalised: "Eaton" yes, "Target" never)."""

    MAX_WORDS = 6

    def __init__(self, names: Iterable[tuple[str, str]], common: frozenset[str]):
        seen: dict[str, set[str]] = {}
        for company_id, name in names:
            full = normalize_name(name)
            if not full:
                continue
            forms = {full}
            words = full.split(" ")
            while len(words) > 1 and words[-1] in _OPTIONAL:
                words = words[:-1]
                forms.add(" ".join(words))
            for f in forms:
                seen.setdefault(f, set()).add(company_id)
        self.by_name: dict[str, str] = {}
        for f, owners in seen.items():
            if len(owners) != 1:
                continue  # two companies answer to it: ambiguous
            words = f.split(" ")
            if len(words) == 1 and (len(f) < 4 or f.isdigit() or f.lower() in STOP
                                    or f.lower() in common or normal(f.lower()) in common):
                continue
            if words[-1].lower() in STOP or words[0].lower() in STOP:
                continue
            self.by_name[f] = next(iter(owners))

    def find(self, sentence: str) -> set[str]:
        """Company ids named in a sentence (whole words; one-word names written capitalised)."""
        tokens = normalize_name(sentence).split(" ")
        found: set[str] = set()
        for i in range(len(tokens)):
            for n in range(min(self.MAX_WORDS, len(tokens) - i), 0, -1):
                key = " ".join(tokens[i:i + n])
                hit = self.by_name.get(key)
                if hit is None:
                    continue
                if n == 1 and not re.search(rf"\b{key[0]}{key[1:].lower()}\b|\b{key}\b", sentence):
                    continue
                found.add(hit)
                break
        return found


@dataclass(frozen=True)
class Mention:
    company_id: str
    named_id: str
    char_start: int
    char_end: int


def mentions(company_id: str, text: str, names: Names, own: Iterable[str] = (),
             base: int = 0) -> list[Mention]:
    """Spec 5: other companies named in a sentence about competition, with its offsets (+ base:
    where the text starts in the document)."""
    skip = set(own) | {company_id}
    out: list[Mention] = []
    for start, end in sentences(text):
        s = text[start:end]
        if not _COMPETE.search(s):
            continue
        for named in sorted(names.find(s) - skip):
            out.append(Mention(company_id, named, base + start, base + end))
    return out


# The job -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Doc:
    company_id: str
    accession_no: str
    filed: object
    parser: str
    parsed_at: object


DOCS_SQL = f"""
WITH latest AS (
  SELECT DISTINCT ON (k.company_id) k.company_id, d.accession_no, d.filed
  FROM bronze.filing_documents d
  JOIN silver.company_ciks k ON k.cik = d.cik
  WHERE d.form = '10-K' AND d.filed > current_date - {MAX_AGE_DAYS}
    AND EXISTS (SELECT 1 FROM silver.filing_sections s
                WHERE s.accession_no = d.accession_no AND s.section_key = '1'
                  AND s.char_end - s.char_start >= {MIN_CHARS}
                  AND s.title !~* 'financial statements')
    AND EXISTS (SELECT 1 FROM gold.financials_coverage v WHERE v.company_id = k.company_id)
  ORDER BY k.company_id, d.filed DESC, d.accession_no DESC
)
SELECT l.company_id, l.accession_no, l.filed, s.parser, s.parsed_at
FROM latest l
CROSS JOIN LATERAL (
  SELECT parser, parsed_at FROM silver.filing_sections s
  WHERE s.accession_no = l.accession_no AND s.section_key = '1'
    AND s.char_end - s.char_start >= {MIN_CHARS} AND s.title !~* 'financial statements'
  ORDER BY parser = 'rust' DESC, parsed_at DESC LIMIT 1) s
ORDER BY l.company_id
"""

NAMES_SQL = """
SELECT a.company_id, a.alias FROM silver.company_aliases a
WHERE a.alias_type = 'name'
  AND EXISTS (SELECT 1 FROM gold.financials_coverage v WHERE v.company_id = a.company_id)
UNION
SELECT c.company_id, c.name FROM silver.companies c
WHERE EXISTS (SELECT 1 FROM gold.financials_coverage v WHERE v.company_id = c.company_id)
"""

TICKERS_SQL = """
SELECT company_id, alias FROM silver.company_aliases WHERE alias_type = 'ticker'
"""


def texts(conn, docs: Sequence[Doc],
          batch: int = FETCH_BATCH) -> Iterator[tuple[Doc, str, int]]:
    """Each document's Item 1 (first TEXT_CHARS characters) and where the section starts in the
    document's text, fetched by primary key in batches."""
    for i in range(0, len(docs), batch):
        part = docs[i:i + batch]
        rows = conn.execute(
            "SELECT accession_no, parser, char_start, left(text, %s) FROM silver.filing_sections"
            " WHERE (accession_no, parser) IN (SELECT * FROM unnest(%s::text[], %s::text[]))"
            "   AND section_key = '1'",
            (TEXT_CHARS, [d.accession_no for d in part], [d.parser for d in part])).fetchall()
        by_key = {(a, p): (t, base) for a, p, base, t in rows}
        for d in part:
            found = by_key.get((d.accession_no, d.parser))
            if found is not None:
                yield d, found[0], found[1]


@dataclass
class Result:
    docs: list[Doc]
    vectors: dict[str, dict[int, float]]
    vocab: Vocabulary
    similar: dict[str, list[Neighbour]]
    found: list[Mention]


@dataclass
class Inputs:
    docs: list[Doc]
    names: list[tuple[str, str]]          # (company_id, a name it has had)
    tickers: dict[str, list[str]]


def compute(read: Callable[[Sequence[Doc]], Iterable[tuple]], inputs: Inputs) -> Result:
    """Two passes over the documents (spec 1-5); nothing written. `read` yields (doc, text) or
    (doc, text, where the section starts in the document's text: mentions are stored as document
    offsets, like a section's own char_start, so the reader opens at them)."""
    vocab = Vocabulary()
    t0 = time.monotonic()
    for _, text, *_ in read(inputs.docs):
        vocab.add(terms(text))
    vocab.close()
    log.info("pass 1: %d documents, %d usable terms, %.0f s", vocab.docs, len(vocab.names),
             time.monotonic() - t0)
    names = Names(inputs.names, vocab.common | ORDINARY)
    by_company: dict[str, list[str]] = {}
    for c, n in inputs.names:
        by_company.setdefault(c, []).append(n)
    vectors: dict[str, dict[int, float]] = {}
    found: list[Mention] = []
    kept: list[Doc] = []
    for d, text, *base in read(inputs.docs):
        own = own_words(by_company.get(d.company_id, []), inputs.tickers.get(d.company_id, []))
        vectors[d.company_id] = vector(terms(text), vocab, own)
        found.extend(mentions(d.company_id, text, names, base=base[0] if base else 0))
        kept.append(d)
    log.info("pass 2: %d vectors, %d competitor mentions, %.0f s", len(vectors), len(found),
             time.monotonic() - t0)
    similar = neighbours(vectors, vocab)
    log.info("similarities: %.0f s", time.monotonic() - t0)
    return Result(kept, vectors, vocab, similar, found)


def write(conn, result: Result) -> dict[str, int]:
    """Replace the three tables in one transaction (readers see old or new, never a mix)."""
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout = '30s'")
        conn.execute("DELETE FROM gold.business_peers")
        conn.execute("DELETE FROM gold.business_mentions")
        conn.execute("DELETE FROM gold.business_profiles")
        with conn.cursor().copy(
                "COPY gold.business_profiles (company_id, accession_no, section_key, filed, parser,"
                " parsed_at, terms, algorithm) FROM STDIN") as cp:
            for d in result.docs:
                cp.write_row((d.company_id, d.accession_no, "1", d.filed, d.parser, d.parsed_at,
                              profile_terms(result.vectors[d.company_id], result.vocab),
                              ALGORITHM))
        peers = 0
        with conn.cursor().copy(
                "COPY gold.business_peers (company_id, peer_id, similarity, terms)"
                " FROM STDIN") as cp:
            for c, rows in result.similar.items():
                for r in rows:
                    cp.write_row((c, r.company_id, r.similarity, list(r.terms)))
                    peers += 1
        by_doc = {d.company_id: d for d in result.docs}
        with conn.cursor().copy(
                "COPY gold.business_mentions (company_id, named_id, accession_no, section_key,"
                " char_start, char_end) FROM STDIN") as cp:
            for m in result.found:
                cp.write_row((m.company_id, m.named_id, by_doc[m.company_id].accession_no, "1",
                              m.char_start, m.char_end))
    return {"profiles": len(result.docs), "peers": peers, "mentions": len(result.found)}


def load_inputs(conn) -> Inputs:
    docs = [Doc(*r) for r in conn.execute(DOCS_SQL).fetchall()]
    tickers: dict[str, list[str]] = {}
    for c, t in conn.execute(TICKERS_SQL).fetchall():
        tickers.setdefault(c, []).append(t)
    return Inputs(docs, [(c, n) for c, n in conn.execute(NAMES_SQL).fetchall()], tickers)


def run(conn) -> dict[str, int]:
    with conn.transaction():
        locked = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,)).fetchone()
    if not (locked and locked[0]):
        log.warning("another parser-peers run holds the lock; exiting")
        return {}
    try:
        with conn.transaction():
            inputs = load_inputs(conn)
        log.info("%d documents, %d names", len(inputs.docs), len(inputs.names))
        with conn.transaction():
            result = compute(lambda docs: texts(conn, docs), inputs)
        return write(conn, result)
    finally:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))


def main() -> None:
    import psycopg

    from tributary_telemetry.logs import configure_logging

    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    configure_logging()
    t0 = time.monotonic()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        counts = run(conn)
    log.info("done in %.0f s: %s", time.monotonic() - t0, counts)


if __name__ == "__main__":
    main()
