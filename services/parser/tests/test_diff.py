"""Section diffs on real NVIDIA sections (fiscal 2026 10-K vs fiscal 2025; Q2 FY2027 10-Q vs
Q1 and vs Q2 FY2026) plus small synthetic cases for each rule in diff.py's docstring."""

import gzip
from functools import cache
from pathlib import Path

from filing_parser.diff import (
    REWRITE_BELOW,
    diff,
    normalize,
    paragraphs,
    render_words,
    word_opcodes,
)
from filing_parser.sections import find_sections, html_to_text

FIXTURES = Path(__file__).parent / "fixtures"


@cache
def section(fixture: str, form: str, key: str) -> str:
    text = html_to_text(gzip.decompress((FIXTURES / f"{fixture}.htm.gz").read_bytes()))
    return next(s.text for s in find_sections(text, form) if s.key == key)


def risk_factors_10k():
    return diff(section("nvda-10-K-prior", "10-K", "1A"), section("nvda-10-K", "10-K", "1A"))


def changed(d, kind, phrase):
    return [c for c in d.changes if c.kind == kind
            and phrase in (c.new.text if c.new else c.old.text)]  # type: ignore[union-attr]


def test_normalize_masks_numbers_and_folds_quotes():
    assert normalize("Revenue for fiscal year 2026 was $215.9 billion, up 65%. NVIDIA’s") == (
        "revenue", "for", "fiscal", "year", "#", "was", "#", "billion", "up", "#", "nvidia's")
    assert normalize("fiscal year 2026", mask_numbers=False) == ("fiscal", "year", "2026")


def test_paragraphs_skip_furniture_and_join_page_breaks():
    text = "\n".join([
        "Item 1A. Risk Factors",
        "Our business could suffer if demand for our products",
        "34",
        "Table of Contents",
        "declines, as it did in a prior year.",
        "Revenue $ 1,234 $ 5,678 12 %",
        "Short label",
        "A separate risk paragraph that stands on its own.",
    ])
    ps = paragraphs(text)
    assert [p.text for p in ps] == [
        "Item 1A. Risk Factors",
        "Our business could suffer if demand for our products declines, as it did in a prior"
        " year.",
        "A separate risk paragraph that stands on its own.",
    ]
    joined = ps[1]
    assert len(joined.spans) == 2
    assert [text[a:b] for a, b in joined.spans] == [
        "Our business could suffer if demand for our products",
        "declines, as it did in a prior year."]


def test_real_paragraph_split_by_running_header_is_joined():
    text = section("nvda-10-K", "10-K", "1A")
    ps = paragraphs(text)
    assert all("Table of Contents" not in p.text for p in ps)
    joined = [p for p in ps if len(p.spans) > 1]
    assert joined
    for p in joined:  # each span is a line of the section text
        assert " ".join(text[a:b].strip() for a, b in p.spans) == p.text


def test_identical_sections_have_no_changes():
    text = section("nvda-10-K", "10-K", "7")
    d = diff(text, text)
    assert d.changes == [] and d.unchanged == d.paragraphs == d.prior_paragraphs


def test_risk_factor_changes_are_substantive():
    d = risk_factors_10k()
    assert d.unchanged + d.rewritten + d.new == d.paragraphs
    assert d.unchanged - d.minor_edits + d.minor_edits + d.rewritten + d.removed == \
        d.prior_paragraphs
    # new risks: the Groq license, the OpenAI partnership
    assert changed(d, "new", "intellectual property license arrangement with Groq")
    assert changed(d, "new", "investment and partnership agreement with OpenAI")
    # the AI Diffusion IFR, rescinded in May 2025, lost its paragraphs
    assert changed(d, "removed", "The AI Diffusion IFR would divide the world into three tiers")
    # rewritten: aligned but below the threshold, with a word diff
    [c] = changed(d, "rewritten", "the USG announced export restrictions and export licensing")
    assert c.similarity is not None and c.similarity < REWRITE_BELOW
    runs = render_words(c.old.text, c.new.text, c.words)  # type: ignore[union-attr,arg-type]
    assert ["-", "During the third quarter of fiscal year 2023,"] in runs
    assert ["+", "In August 2022,"] in runs


def test_numbers_only_changes_are_unchanged():
    d = diff(section("nvda-10-K-prior", "10-K", "7"), section("nvda-10-K", "10-K", "7"))
    assert d.numbers_only > 0
    assert not changed(d, "rewritten", "Revenue for fiscal year 2026 was")
    assert not changed(d, "new", "Revenue for fiscal year 2026 was")


def test_10q_against_prior_quarter_and_year_ago():
    new = section("nvda-10-Q", "10-Q", "II-1A")
    prior = diff(section("nvda-10-Q-prior", "10-Q", "II-1A"), new)
    year_ago = diff(section("nvda-10-Q-year-ago", "10-Q", "II-1A"), new)
    for d in (prior, year_ago):
        assert d.paragraphs == len(paragraphs(new))
        assert d.new + d.removed + d.rewritten > 0
    md_a = diff(section("nvda-10-Q-prior", "10-Q", "I-2"), section("nvda-10-Q", "10-Q", "I-2"))
    assert md_a.numbers_only > 0


def test_diff_is_deterministic():
    a, b = risk_factors_10k(), risk_factors_10k()
    assert [(c.kind, c.new and c.new.spans, c.old and c.old.spans) for c in a.changes] == \
        [(c.kind, c.new and c.new.spans, c.old and c.old.spans) for c in b.changes]


def test_moved_paragraph_is_unchanged_and_order_follows_new_filing():
    one = "First risk paragraph about supply chains and vendors."
    two = "Second risk paragraph about customers and demand cycles."
    three = "Third risk paragraph about regulation and export rules."
    d = diff("\n".join([one, two, three]), "\n".join([three, one, "A brand new risk about"
                                                     " energy supply for datacenters.", two]))
    assert d.unchanged == 3 and [c.kind for c in d.changes] == ["new"]


def test_removed_paragraph_sits_after_its_preceding_neighbour():
    a = "Alpha paragraph with enough words to count."
    b = "Bravo paragraph with enough words to count here."
    gone = "Charlie paragraph that will be removed from the filing."
    c = "Delta paragraph with enough words to count again."
    new_one = "Echo paragraph that is entirely new in this filing."
    d = diff("\n".join([a, b, gone, c]), "\n".join([new_one, a, b, c]))
    assert [(x.kind, (x.new or x.old).text) for x in d.changes] == [  # type: ignore[union-attr]
        ("new", new_one), ("removed", gone)]


def test_minor_edit_counts_as_unchanged_and_rewrite_below_threshold():
    base = ("We depend on a limited number of third parties to manufacture, assemble, test and"
            " package our products, which reduces our control over product quality.")
    minor = base.replace("reduces", "limits")
    rewrite = ("We depend on a small number of third parties to manufacture, test and package"
               " our products, and their capacity limits have reduced our revenue.")
    d = diff(base, minor)
    assert (d.minor_edits, d.changes) == (1, [])
    d = diff(base, rewrite)
    assert [c.kind for c in d.changes] == ["rewritten"]


def test_word_opcodes_render_both_texts():
    old = "Sales to Customer A were 12% of revenue in fiscal 2025."
    new = "Sales to customer A and Customer B were 15% of revenue in fiscal 2026."
    runs = render_words(old, new, word_opcodes(old, new))
    assert " ".join(t for op, t in runs if op != "-") == new
    assert " ".join(t for op, t in runs if op != "+") == \
        "Sales to customer A were 12% of revenue in fiscal 2025."  # "=" shows the new casing
    assert ["-", "12%"] in runs and ["+", "15%"] in runs
