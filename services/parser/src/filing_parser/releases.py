"""Quarterly figures of a foreign filer's results release (a 6-K's own tables; migration 165).

A company filing 20-F or 40-F reports its quarters only in 6-K results releases, which Tributary
stores (095: ops.results_release) and the parsers split into a "6-K" section and its EX-99.x
exhibits, tables in silver.filing_tables (rule 11). This reader takes a release's headline figures
from those stored tables, independently of any XBRL, for gold to read where no XBRL figure of the
metric ends near the period (gold.usable_facts, 165: after every tag, checked against the
company's own 20-F/40-F). Pure: no I/O (index.py reads the tables and writes
silver.release_figures). Reuses statements.py's cells, numbers, dates and words: a change there is
a change here too. The rules; bump READER with any change (the catch-up kind 'releases' then
re-reads every stored 6-K):

1. Cells and numbers: statements.py rule 1. A data row's label is its first non-empty cell.
2. Periods. A period cell is a head cell naming a date (statements.py's; a numeric one only when it
   reads one way: "9/30/2026", "30.09.2026", "2026-09-30"), one year, or a quarter, half or nine
   months by label ("Q3 2026", "3Q26", "Q3'26", "Q3 FY26", "third quarter 2026", "H1 2026", "1H26",
   "first half 2026", "9M26"); head cells above it naming at most one year complete it
   (statements.py rule 2). Its length: statements.py's words, a quarter label 3, "half year" or a
   half label 6, a nine-month label 9, "full year" / "FY26" / "twelve months" 12. Columns a head
   marks as a change, a percentage, a variance or constant currency hold no figure. A row whose
   other numbers aren't each under a period isn't read (misaligned), unless it has exactly as many
   numbers as periods (read in order).
3. Current. The release's period is its period of report, unless no table of figures states a
   period ending within 7 days of it (a 6-K dated by its filing): then the latest such period
   ending on or before it, within 135 days. Only periods ending within 7 days of the release's
   period are read: comparatives are other releases' figures. A label ends at its fiscal quarter's
   end (the company's fiscal year end month; "Q3 FY26" of a March year is December 2025) or at its
   calendar quarter's (a March year's "Q3 2025" as September 2025), whichever is the release's.
4. Scale, from the table's head, the 8 short lines before it, a heading or units row inside it
   ("Financial position (billions of Canadian dollars)", "Balance sheet | £bn | £bn" scale the rows
   below) or the row's own label ("Revenue ($ millions)", "Revenue – R million"): statements.py
   rule 4's words and "million(s)" / "thousand(s)" / "billion(s)" / "m" / "bn" / "'000" after a
   currency ("NT$ million", "RMB'000", "€m", "(Unit: NT$ million)", "in € millions"). A scale of
   shares, tonnes, customers or the like isn't money's. With no scale named a figure is read as
   printed and flagged (scale_named): gold reads it only where its size checks out against the
   company's 20-F/40-F, the check every release's revenue and total assets pass.
5. Currency, named in the scale's own text, else in the table's head: an ISO code or a currency
   word or sign (NT$ TWD, US$ USD, RMB CNY, € EUR, £ GBP, C$ CAD, R$ BRL, R ZAR, ...). A bare "$"
   or "dollars" is "$"; ¥ alone (yen or yuan) and none named are None. Gold reads "$" as the
   company's currency only when that is a dollar currency, None as the company's currency.
6. Lines, by label (statements.py's label_key; dashes read alike; a printed scale, "reported" and a
   footnote digit run into the last word dropped):
     revenue           (total) (net) (operating) revenue(s) / sales (, net) / turnover; revenue(s)
                       from (contracts with) customers; total revenues and other income; (total)
                       net sales and revenues; sales and other operating revenues
     net_income        (net) income / earnings / profit / loss, either way round with "(loss)",
                       for the period / quarter / year, and comprehensive income (loss),
                       attributable to the owners, shareholders, stockholders or equity holders
                       (of the company, the parent ...); or a row naming them under an
                       "attributable to" heading that follows net income (never comprehensive
                       income)
     operating_income  operating income / profit / loss, income / profit / loss from operations,
                       results from operating activities
     total_assets      total assets
   Never a label naming adjusted, underlying, core, non-GAAP or non-IFRS, normalised, recurring,
   pro forma, excluding, before, per share, margin, %, segment, continuing or discontinued
   operations, minority or non-controlling interests, preferred, class, common or average holders,
   or "available to". Signs: a label naming only a loss ("net loss", "total operating loss") reads
   every number as a loss; a row printing one both ways is left out (sign). Revenue and total
   assets are never zero or less (not_positive).
7. Tables not read: a part of the company (a parent's, separate or stand-alone statements,
   guarantors, issuers, subsidiaries, segments, joint ventures, associates; "intercompany" rows),
   adjusted, pro forma or reconciliation views, and movements (opening and closing balances), by
   the table's head, its short lines before or, for a part of the company by name, anywhere in the
   600 characters before it.
8. One figure per metric and period (end, length). Net income and operating income only from a
   table that gives the period's revenue as read (a segment's or a subsidiary's table gives
   another), else printed alike in two tables or more (unanchored). Then net income attributable to
   the parent's owners over the period's other net income figures, and a titled table (a statement
   of income, operations, profit or loss, comprehensive income, financial position or condition; a
   balance sheet; an income or earnings statement; "consolidated results", "financial highlights")
   over the rest when it has candidates. Candidates agree when within 0.5% of the largest or the
   coarser one's last printed digit (one figure in two scales: the most precise is read);
   disagreeing ones are left out (ambiguous).

Every figure keeps where it came from: section, table, row, the cell's column, its label as
printed, the scale and currency read. `left` counts what was left out, by reason. Measured against
the XBRL that ~470 companies' 6-Ks carry, and the 20-F/40-F years of those that carry none:
migration 165's header.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from filing_parser import statements
from filing_parser.statements import (
    _MONTHS,
    _Cell,
    _cells,
    _dates,
    _head,
    _months,
    label_key,
    number,
)

READER = "releases-1"

METRICS = ("revenue", "net_income", "operating_income", "total_assets")
INSTANT = frozenset({"total_assets"})
ANCHORED = frozenset({"net_income", "operating_income"})
CURRENT_DAYS = 7
AGREE = Decimal("0.005")
TITLE_LINES, TITLE_CHARS = 8, 150

_YEAR4 = r"(?:19|20)\d{2}"
_YEARS = re.compile(rf"(?<![\d,.$])({_YEAR4})(?![\d,])")
_YEAR2 = r"(?:'|’)?((?:19|20)\d{2}|\d{2})"
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3,
            "4th": 4}
_QUARTER_LABEL = re.compile(
    r"(?<![a-z0-9])(?:q([1-4])|([1-4])q)(?:\s*(?:fy|f)?\s*" + _YEAR2 + r")?(?![a-z0-9])", re.I)
_QUARTER_WORDS = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:fiscal\s+)?quarter"
    r"(?:\s+(?:of\s+)?(?:fiscal\s+(?:year\s+)?)?(" + _YEAR4 + r"))?", re.I)
_HALF_LABEL = re.compile(
    r"(?<![a-z0-9])(?:h([12])|([12])h)(?:\s*(?:fy)?\s*" + _YEAR2 + r")?(?![a-z0-9])", re.I)
_HALF_WORDS = re.compile(r"\b(first|second|1st|2nd)\s+half(?:[\s-]+year)?"
                         r"(?:\s+(?:of\s+)?(?:fiscal\s+)?(" + _YEAR4 + r"))?", re.I)
_NINE_LABEL = re.compile(r"(?<![a-z0-9])9\s*m(?:\s*" + _YEAR2 + r")?(?![a-z0-9])", re.I)
_LENGTH = re.compile(r"\b(?:half[\s-]?year(?:ly)?|semi[\s-]?annual|interim\s+period)\b", re.I)
_FULL_YEAR = re.compile(r"\b(?:full[\s-]year|fy\s*'?\d{2,4}|twelve\s+months|12\s+months)\b", re.I)
_NOT_PERIOD = re.compile(r"\bvs\.?\b|\bversus\b|change|variance|\bgrowth\b|%", re.I)

# Scale (rule 4): statements.py's words, and "million(s)" etc. after a currency
_RAND = r"r(?=\s*(?:'000|m\b|mn\b|million|bn\b|billion|thousand))"  # "R million": rand
_CUR = (rf"(?:{_RAND}|nt\$|us\$|u\.s\.\s*\$|hk\$|c\$|a\$|r\$|s\$|nz\$|rmb|cny|eur|gbp|usd|cad|"
        r"aud|chf|jpy|"
        r"twd|hkd|inr|krw|brl|mxn|sek|nok|dkk|zar|ils|nis|try|sgd|php|idr|clp|cop|pen|ars|"
        r"rub|pln|huf|czk|nzd|thb|myr|egp|ngn|sar|aed|qar|kzt|uah|vnd|\$|€|£|¥|₹|rs\.?|ps\.?)")
_SCALE_WORD = {"thousand": 1_000, "thousands": 1_000, "000": 1_000, "k": 1_000,
               "million": 1_000_000, "millions": 1_000_000, "mn": 1_000_000, "mm": 1_000_000,
               "m": 1_000_000, "billion": 1_000_000_000, "billions": 1_000_000_000,
               "bn": 1_000_000_000}
_SCALE_MORE = re.compile(
    r"(?<![a-z])" + _CUR + r"\s*(?:in\s+)?'?’?(thousands?|millions?|billions?|000|mn|mm|bn|m|k)"
    r"(?![a-z])"
    r"|\b(thousands?|millions?|billions?)\s+of\s+(?:[a-z.]+\s+){0,2}(?:dollars|euros?|pounds|"
    r"yen|yuan|renminbi|won|rupees|reais|reals|pesos|francs|krona|kronor|kroner|rand|shekels|"
    r"lira|ringgit|baht|rupiah|rubles|zloty)\b"
    r"|\bin\s+" + _CUR + r"\s*(thousands?|millions?|billions?|000|mn|bn|m)(?![a-z])"
    r"|\b(?:unit|units|amounts?|figures)\s*:\s*" + _CUR + r"?\s*(thousands?|millions?|billions?)\b",
    re.I)

# Currency (rule 5): the first match in the scale's text wins; order matters (NT$ before $)
_CURRENCIES = [
    (r"nt\$|new taiwan dollars?|\btwd\b", "TWD"),
    (r"us\$|u\.s\.\s*\$|u\.?s\.?\s+dollars?|united states dollars?|\busd\b", "USD"),
    (r"hk\$|hong kong dollars?|\bhkd\b", "HKD"),
    (r"(?<![a-z])c\$|canadian dollars?|\bcad\b|\bcdn\$?", "CAD"),
    (r"(?<![a-z])a\$|australian dollars?|\baud\b", "AUD"),
    (r"nz\$|new zealand dollars?|\bnzd\b", "NZD"),
    (r"(?<![a-z])s\$|singapore dollars?|\bsgd\b", "SGD"),
    (r"(?<![a-z])r\$|\breais\b|\breals?\b|\bbrl\b", "BRL"),
    (r"\brmb\b|renminbi|\bcny\b|\byuan\b", "CNY"),
    (r"€|\beuros?\b|\beur\b", "EUR"),
    (r"£|pounds? sterling|\bgbp\b|\bsterling\b", "GBP"),
    (r"\bjpy\b|\byen\b", "JPY"),
    (r"₹|\binr\b|\brupees\b|\brs\.?\s", "INR"),
    (r"\bkrw\b|\bwon\b", "KRW"),
    (r"\bmxn\b|mexican pesos?", "MXN"),
    (r"\bchf\b|swiss francs?", "CHF"),
    (r"\bsek\b|swedish kron", "SEK"),
    (r"\bnok\b|norwegian kron", "NOK"),
    (r"\bdkk\b|danish kron", "DKK"),
    (rf"\bzar\b|\brand\b|(?<![a-z]){_RAND}", "ZAR"),
    (r"\bils\b|\bnis\b|shekels?", "ILS"),
    (r"\bclp\b|chilean pesos?", "CLP"),
    (r"\bcop\b|colombian pesos?", "COP"),
    (r"\bars\b|argentine pesos?", "ARS"),
    (r"\bpen\b|\bsoles\b", "PEN"),
    (r"\btry\b|turkish lira", "TRY"),
    (r"\bphp\b|philippine pesos?", "PHP"),
    (r"\bidr\b|rupiah", "IDR"),
    (r"\bpln\b|zloty", "PLN"),
    (r"\bthb\b|\bbaht\b", "THB"),
    (r"\bmyr\b|ringgit", "MYR"),
    (r"\brub\b|roubles?|rubles?", "RUB"),
    (r"\bkzt\b|tenge", "KZT"),
    (r"\$|\bdollars?\b", "$"),
]
_CURRENCY = [(re.compile(p, re.I), code) for p, code in _CURRENCIES]
DOLLAR = "$"

_TITLE = re.compile(
    r"statements?\s+of\s+(?:consolidated\s+|condensed\s+|interim\s+|unaudited\s+)*"
    r"(?:operations|income|earnings|profit\s+(?:or|and)\s+loss|(?:comprehensive\s+)?income|"
    r"comprehensive\s+(?:income|loss)|financial\s+(?:position|condition)|condition|"
    r"assets\s+and\s+liabilities)"
    r"|\b(?:income|earnings|profit\s+and\s+loss)\s+statements?|balance\s+sheets?"
    r"|consolidated\s+results|financial\s+highlights", re.I)

# Rule 7: tables of a part of the company, a movement or an adjustment (head or the short lines
# before it): not read
_NOT_TABLE = re.compile(
    r"parent\s+(?:company|only|entity)|\(parent\)|\bregistrant\b|guarantor|obligor|\bissuers?\b|"
    r"consolidating|(?<!and )(?<!and its )(?<!& )subsidiar|"
    r"\bseparate\b|stand-?alone|non-?consolidated|unconsolidated|company\s+only|bank\s+only|"
    r"segment|joint\s+venture|associates?\b|pro\s?forma|non-?gaap|non-?ifrs|reconcil|"
    r"adjusted|underlying|\bcore\b|by\s+(?:region|geograph|business|division|product)", re.I)
# ... and, anywhere in the text before it or in a row's label, a part of the company by name
_PART_OF = re.compile(r"guarantor|obligor|intercompany|inter-company|parent\s+company|"
                      r"condensed\s+consolidating|issuers?\s+and|parent\s+only", re.I)
_MOVEMENT = re.compile(r"^(?:opening|closing) balance|^balance,? (?:at|as of) (?:the )?"
                       r"(?:beginning|end)|^balance at \w+ \d", re.I)
# a scale naming a quantity other than money ("Shares (thousands)", "tonnes 000")
_NOT_MONEY = re.compile(r"shares?|\bunits\b(?!\s*:)|tonnes?|tons?\b|ounces?|\boz\b|barrels?|\bboe|"
                        r"\bmwh|\bgwh|\bkwh|customers?|employees?|subscribers?|passengers?|"
                        r"vehicles?|stores?|square|\bsq\b|carats?|lbs?\b|pounds of|bushels?|"
                        r"\bbcf|\bmcf|cubic|hectol|litres?|liters?|gallons?|visits?|users?", re.I)
# a units row inside a table: every value cell a scale or a currency ("£bn", "US$m", "%")
_UNIT_CELL = re.compile(r"^\(?\s*(?:[a-z]{0,3}\$|[€£¥₹]|[a-z]{3})?\s*'?(?:000|m|mn|bn|k|"
                        r"thousands?|millions?|billions?)?\s*\)?$|^%$", re.I)
_CHANGE = re.compile(r"%|change|variance|\bvar\b|growth|\bvs\.?\b|versus|\bcer\b|^notes?$",
                     re.I)

_NOT_LINE = re.compile(
    r"adjust|underlying|\bcore\b|non-?gaap|non-?ifrs|normali[sz]|recurring|pro\s?forma|"
    r"excluding|\bbefore\b|per\s+(?:share|ads|unit)|margin|%|segment|continuing|discontinued|"
    r"minority|non-?controlling|available\s+to|\bebit|\bebitda|comparable|organic|constant|"
    r"guidance|outlook|forecast|budget|\bper\b|preferred|\bclass\b|\bcommon\b|average", re.I)
_OWNER_WORDS = (r"(?:owners?|shareholders?|stockholders?|equity\s+holders?|holders\s+of\s+"
                r"(?:ordinary\s+)?shares|members)")
_OWNERS = (rf"(?:[a-z][\w&.'-]*\s+){{0,4}}{_OWNER_WORDS}"
           r"(?:\s+of\s+(?:the\s+)?[a-z][\w&.,' -]{0,60})?")
_LOSS_ONLY = re.compile(r"^(?:total )?(?:net |operating )?loss(?:es)?\b"
                        r"(?!.*\b(?:income|profit|earnings|gain)\b)"
                        r"|^loss from operations\b(?!.*\b(?:income|profit)\b)")
_EITHER = r"(?: ?(?:/|\(|or |and )? ?\(?(?:loss|income|profit|earnings)\)?)?"
_PATTERNS = {
    "revenue": re.compile(
        r"^(?:total )?(?:net )?(?:operating )?(?:revenues?|sales|turnover)(?:,? net)?$"
        r"|^(?:total )?(?:net )?revenues? from (?:contracts with )?customers$"
        r"|^total (?:net )?revenues? and other income$"
        r"|^(?:total )?net sales and (?:other )?(?:operating )?revenues?$"
        r"|^(?:total )?sales and other operating revenues?$"),
    "net_income": re.compile(
        r"^(?:net )?(?:income|earnings|profit|loss|\(loss\))" + _EITHER +
        r"(?: (?:for|of) the (?:period|quarter|year|half[- ]year|three months|six months|"
        r"nine months|twelve months))?"
        r"(?: and (?:total )?comprehensive (?:income|loss|\(loss\) income|income \(loss\)))?"
        rf"(?:,? attributable to (?:the )?{_OWNERS})?$"),
    "operating_income": re.compile(
        r"^(?:total )?operating (?:income|profit|loss|\(loss\))" + _EITHER + "$"
        r"|^(?:income|profit|loss|\(loss\))" + _EITHER + r" from operations$"
        r"|^results from operating activities$"),
    "total_assets": re.compile(r"^total assets$"),
}
# net income read only with a net, profit or loss word, a period or "attributable" ("income" alone
# is too wide: "Income" over a REIT's lines, a bank's "Income" subtotal)
_NET_WORD = re.compile(r"^(?:net |profit|loss|\(loss\))|(?:for|of) the |attributable")
_ATTRIBUTABLE_HEADING = re.compile(r"^(?:(?:net )?(?:income|earnings|profit|loss)\b.*\s)?"
                                   r"attributable to$")
_OWNER_ROW = re.compile(rf"^{_OWNERS}$|^(?:the )?(?:company|parent|bank|group)$")
_REPORTED = re.compile(r"\s*(?:-\s*|,\s*|\()?(?:as\s+)?reported\)?$"
                       r"|\s*\((?:ifrs|gaap|unaudited)\)$")
_ROW_SCALE = re.compile(r"\s*(?:\(|-|,)\s*(?:in\s+)?(?:[$€£¥]|[a-z]{1,3}\$?)?\s*(?:in\s+)?"
                        r"(?:thousands?|millions?|billions?|'000|000|m|mn|bn)\)?$")
_NUMBER_CELL = re.compile(r"\d")


@dataclass(frozen=True)
class Figure:
    metric: str
    value: Decimal              # as printed x scale, signed as rule 6 reads it
    scale: int
    precision: Decimal          # the printed last digit's worth (scale, or a tenth of it, ...)
    scale_named: bool           # False: no scale in the table, read as printed (rule 4)
    currency: str | None        # ISO code, "$" (a bare dollar) or None (rule 5)
    period_end: date
    months: int | None          # the period's length; None for an instant (total assets)
    label: str                  # as printed
    section_key: str
    table_no: int
    row_no: int
    col_no: int                 # the cell's index in its row
    titled: bool
    attributable: bool          # net income attributable to the parent's owners
    statement: int = -1         # the first table of its statement (a table continued after a page
                                # break is one statement with it: rule 8's anchor)


@dataclass
class Read:
    figures: list[Figure] = field(default_factory=list)
    left: dict[str, int] = field(default_factory=dict)  # reason -> candidates left out
    period: date | None = None  # the release's own period (rule 3)

    def leave(self, reason: str, n: int = 1) -> None:
        self.left[reason] = self.left.get(reason, 0) + n


_NUMERIC_DATE = re.compile(r"(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.]((?:19|20)?\d{2})(?![\d/.])")
_MONTH_YEAR = re.compile(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s'’-]*"
                         r"(\d{2})\s*$", re.I)
_ISO_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)")


def _all_dates(text: str) -> list[date]:
    """statements.py's dates, and numeric ones read one way only ("9/30/2026", "30.09.2026",
    "2026-09-30"; "6/7/2026" could be either and isn't read)."""
    out = _dates(text)
    for a, b, y in _NUMERIC_DATE.findall(text):
        year = int(y) + (2000 if len(y) == 2 else 0)
        ways = set()
        for mo, d in ((int(a), int(b)), (int(b), int(a))):
            try:
                ways.add(date(year, mo, d))
            except ValueError:
                pass
        if len(ways) == 1:
            out += list(ways)
    for y, mo, d in _ISO_DATE.findall(text):
        try:
            out.append(date(int(y), int(mo), int(d)))
        except ValueError:
            pass
    m = _MONTH_YEAR.match(text)  # a whole cell "Sep 26": the month's end, 2026
    if m and not out:
        out.append(_month_end(2000 + int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]))
    return out


@dataclass(frozen=True)
class _Period:
    first: int
    last: int
    ends: tuple[date, ...]      # the end dates it may mean (a date, or a label's two readings)
    months: int | None


def _month_end(y: int, m: int) -> date:
    return date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)


def _fiscal_end(fye_month: int, fiscal_year: int, months_in: int) -> date:
    """The end of the period `months_in` months into the fiscal year named `fiscal_year` (the
    calendar year its last month, `fye_month`, falls in)."""
    k = (fiscal_year - 1) * 12 + fye_month + months_in - 1  # zero-based month index of the end
    return _month_end(k // 12, k % 12 + 1)


def _label_ends(text: str, fye_month: int) -> tuple[list[date], int | None]:
    """Rule 3: a quarter, half or nine-month label's possible ends (its fiscal quarter's, then its
    calendar quarter's) and its length."""
    years = sorted({int(y) for y in _YEARS.findall(text)} | {d.year for d in _dates(text)})
    found: list[tuple[int, int, str | None]] = []  # (months into the year, length, year text)
    for m in _QUARTER_LABEL.finditer(text):
        found.append((3 * int(m.group(1) or m.group(2)), 3, m.group(3)))
    for m in _QUARTER_WORDS.finditer(text):
        found.append((3 * _ORDINAL[m.group(1).lower()], 3, m.group(2)))
    for m in _HALF_LABEL.finditer(text):
        found.append((6 * int(m.group(1) or m.group(2)), 6, m.group(3)))
    for m in _HALF_WORDS.finditer(text):
        found.append((6 * _ORDINAL[m.group(1).lower()], 6, m.group(2)))
    for m in _NINE_LABEL.finditer(text):
        found.append((9, 9, m.group(1)))
    if not found or len({(into, n) for into, n, _ in found}) > 1:
        return [], None  # no label, or two over one column ("Q1-Q3"): not read
    into, length, ytext = found[0]
    if ytext:
        y = int(ytext.lstrip("'’"))
        years = [y + 2000 if y < 100 else y]
    ends: list[date] = []
    for y in years:
        ends.append(_fiscal_end(fye_month, y, into))
        ends.append(_month_end(y, into))
    return list(dict.fromkeys(ends)), length


def _length(text: str) -> int | None:
    """Rule 2's length named in words (statements.py's, a half year and a full year too)."""
    if _LENGTH.search(text):
        return 6
    if _FULL_YEAR.search(text):
        return 12
    return _months(text)


def _periods(head: list[list[_Cell]], fye_month: int) -> list[_Period]:
    """Rule 2's period cells, left to right, each with its possible ends and length."""
    def is_period(c: _Cell) -> bool:
        t = c.text.lower()
        if not t.strip() or _NOT_PERIOD.search(t):
            return False
        return bool(_all_dates(t)) or len(set(_YEARS.findall(t))) == 1 \
            or bool(_label_ends(t, 12)[0])

    best = max(head, key=lambda g: sum(is_period(c) for c in g), default=[])
    out = []
    for c in best:
        if not is_period(c):
            continue
        text = " ".join(h.text for g in head for h in g
                        if h.text.strip() and h.first < c.last and c.first < h.last
                        and (h is c or g is not best
                             and len(set(_YEARS.findall(h.text.lower()))) <= 1)).lower()
        dates = _all_dates(text) or _all_dates(c.text.lower())
        if dates:
            ends, length = list(dict.fromkeys(dates)), _length(text)
        else:
            ends, length = _label_ends(text, fye_month)
            if length is None:
                length = _length(text)
        out.append(_Period(c.first, c.last, tuple(ends), length))
    return out


def scale_and_currency(texts: Iterable[str]) -> tuple[int | None, str | None]:
    """Rules 4-5 over texts nearest first: the first naming a scale gives it and the currency it
    names."""
    for t in texts:
        t = t.replace("’", "'").replace("\xa0", " ")
        if (s := _scale(t)) is not None:
            return s, _currency(t)
    return None, None


def _scale(text: str) -> int | None:
    if _NOT_MONEY.search(re.sub(r"\bexcept\b.*", "", text, flags=re.I | re.S)):
        return None  # rule 4: a scale of shares, tonnes or customers isn't money's
    m = _SCALE_MORE.search(text)
    if m:
        return _SCALE_WORD[next(g for g in m.groups() if g).lower()]
    return statements.scale_of([text], "")


def _currency(text: str) -> str | None:
    """Rule 5: the currency a text names; ¥ alone (yen or yuan) names none."""
    low = text.lower()
    return next((code for p, code in _CURRENCY if p.search(low)), None)


def key_of(label: str) -> str:
    """Rule 6's label: statements.py's label_key, dashes as one, a printed scale, "reported" and
    a footnote digit run into the last word dropped."""
    key = label_key(label.replace("–", "-").replace("—", "-"))
    key = _ROW_SCALE.sub("", key).strip()
    key = _REPORTED.sub("", key).strip()
    key = re.sub(r"(?<=[a-z)])\d{1,2}$", "", key).strip()
    return key.rstrip(":").strip()


def line_of(key: str, heading: str, above: str | None) -> tuple[str | None, bool]:
    """Rule 6: the metric a label reads as, and whether it is net income attributable to the
    parent's owners. `heading` is the label-only row above; `above` the metric of the data row
    before that heading (an "Attributable to:" under net income)."""
    if _NOT_LINE.search(key):
        return None, False
    if _OWNER_ROW.match(key) and _ATTRIBUTABLE_HEADING.match(heading) and (
            heading != "attributable to" or above == "net_income"):
        return "net_income", True
    for name, p in _PATTERNS.items():
        if p.match(key):
            if name == "net_income" and not _NET_WORD.search(key):
                return None, False
            return name, name == "net_income" and "attributable" in key
    return None, False


def _decimals(text: str) -> int:
    m = re.search(r"\.(\d+)", text)
    return len(m.group(1)) if m else 0


def _labels(grid: list[list[_Cell]], rows: Sequence[dict]) -> list[tuple[int, str] | None]:
    """statements.py rule 1's data rows: each row's label (where its cells end, its text), None
    for a head row or a row without a number after a label (a row of bare years is a head)."""
    out: list[tuple[int, str] | None] = []
    for i, cells in enumerate(grid):
        texts = [c for c in cells if c.text.strip()]
        lab = None
        if texts and number(texts[0].text) is None and not rows[i].get("head"):
            nums = [c for c in cells if c.first >= texts[0].last and number(c.text) is not None]
            if nums and not all(re.match(r"^(?:19|20)\d{2}$", c.text.strip()) for c in nums):
                lab = (texts[0].last, texts[0].text)
        out.append(lab)
    return out


def _titled(head_texts: Sequence[str], before: str) -> bool:
    lines = [x for x in before.split("\n") if x.strip()][-TITLE_LINES:]
    return any(_TITLE.search(x)
               for x in [*head_texts, *(x for x in lines if len(x) <= TITLE_CHARS)])


def read_table(rows: Sequence[dict], before: str, period: date, fye_month: int, *,
               section_key: str = "", table_no: int = 0, statement: int | None = None,
               read: Read | None = None) -> list[Figure]:
    """Rules 1-7 over one stored table and the text before it: its current candidates.
    `statement`: the table this one continues (else itself)."""
    read = read if read is not None else Read()
    grid = [_cells(r) for r in rows]
    labels = _labels(grid, rows)
    first_data = next((i for i, lab in enumerate(labels) if lab is not None), None)
    if first_data is None:
        return []
    head = _head(grid, rows, first_data)
    head_texts = [c.text for g in head for c in g if c.text.strip()]
    near = [x for x in before.split("\n") if x.strip()][-TITLE_LINES:]
    short = [*head_texts, *(x for x in near if len(x) <= TITLE_CHARS)]
    if any(_NOT_TABLE.search(t) for t in short) or _PART_OF.search(before) \
            or any(lab and _PART_OF.search(lab[1]) for lab in labels):
        return []  # rule 7: a part of the company, or an adjusted view
    if any(lab and _MOVEMENT.match(label_key(lab[1])) for lab in labels):
        return []  # rule 7: a movement (opening and closing balances), not the period's figures
    table_scale = scale_and_currency([*head_texts, *reversed(near)])
    if table_scale[0] is not None and table_scale[1] is None:  # rule 5: else the head's currency
        table_scale = (table_scale[0], next((c for t in head_texts if (c := _currency(t))), None))
    # rule 2: columns a head marks as a change or a percentage hold no figure
    changes = [(c.first, c.last) for g in head for c in g if c.text.strip()
               and _CHANGE.search(c.text) and not _dates(c.text.lower())
               and not _label_ends(c.text.lower(), 12)[0]
               and not _YEARS.search(c.text)]
    titled = _titled(head_texts, before)
    periods = _periods(head, fye_month)
    current = {k for k, p in enumerate(periods)
               if any(abs((e - period).days) <= CURRENT_DAYS for e in p.ends)}
    if not current:
        return []
    out: list[Figure] = []
    heading, above, last = "", None, None  # last: the metric of the data row before
    row_scale = table_scale
    for i, (cells, lab) in enumerate(zip(grid, labels, strict=True)):
        texts = [c.text for c in cells if c.text.strip()]
        if i >= first_data and len(texts) > 1 \
                and all(_UNIT_CELL.match(t.strip()) for t in texts[1:]):
            # rule 4: a units row inside the table ("Balance sheet | £bn | £bn") scales the rows
            # below it
            s, cur = scale_and_currency(texts[1:])
            if s is not None:
                row_scale = (s, cur or table_scale[1])
            heading, above = key_of(texts[0]), last
            continue
        if lab is None:
            if len(texts) == 1 and not _NUMBER_CELL.fullmatch(texts[0]) and (
                    i >= first_data or not (_all_dates(texts[0].lower())
                                            or _YEARS.search(texts[0]))):
                heading, above = key_of(texts[0]), last
                s, cur = scale_and_currency([texts[0]])
                if s is not None:  # rule 4: a heading row naming a scale scales the rows below
                    row_scale = (s, cur or table_scale[1])
            continue
        raw = lab[1]
        key = key_of(raw)
        if key == "total" and heading:  # statements.py rule 5: "Total" under "Revenues:"
            key = heading if heading.startswith("total ") else f"total {heading}"
        metric, attributable = line_of(key, heading, above)
        last = metric
        if metric is None:
            continue
        scale, currency = row_scale
        own = scale_and_currency([raw])
        if own[0] is not None:
            scale, currency = own[0], own[1] or currency
        nums = [(c, v, j) for j, c in enumerate(cells)
                if c.first >= lab[0] and (v := number(c.text)) is not None
                and not any(a <= c.first < b for a, b in changes)]
        if len(nums) == len(periods):
            hits = [(k, c, v, j) for k, (c, v, j) in enumerate(nums)]
        else:
            hits = [(k, c, v, j) for c, v, j in nums for k, p in enumerate(periods)
                    if p.first <= c.first < p.last]
            if len(hits) < len(nums):
                if any(h[0] in current for h in hits) or not hits:
                    read.leave("misaligned")  # rule 2: a number under no period
                continue
        hits = [h for h in hits if h[0] in current]
        if not hits:
            continue
        named = scale is not None
        if not named:
            scale = 1  # rule 4: as printed; gold reads it only where its size checks out
        loss_only = bool(_LOSS_ONLY.match(key))
        if loss_only and len({"(" in c.text for c, v, _ in nums if v != 0}) > 1:
            read.leave("sign", len(hits))  # rule 6: a loss line printed both ways
            continue
        for k, c, v, j in hits:
            p = periods[k]
            months = None if metric in INSTANT else p.months
            if metric not in INSTANT and months is None:
                read.leave("no_length")
                continue
            end = next(e for e in p.ends if abs((e - period).days) <= CURRENT_DAYS)
            value = -abs(v) if loss_only else v
            if metric in ("revenue", "total_assets") and value <= 0:
                read.leave("not_positive")  # rule 6: revenue and assets are never zero or less
                continue
            out.append(Figure(metric, value * scale, scale,
                              Decimal(scale) / (10 ** _decimals(c.text)), named, currency, end,
                              months,
                              raw[:500], section_key, table_no, i, j, titled, attributable,
                              table_no if statement is None else statement))
    return out


def _agree(pool: list[Figure]) -> bool:
    """Rule 7: within 0.5% of the largest, or within the coarser figure's last printed digit."""
    top = max(abs(f.value) for f in pool)
    slack = max(AGREE * top, max(f.precision for f in pool))
    return all(abs(f.value - pool[0].value) <= slack for f in pool)


def choose(candidates: list[Figure], read: Read) -> list[Figure]:
    """Rule 7: one figure per metric and period, or none."""
    groups: dict[tuple, list[Figure]] = {}
    revenue: dict[tuple, set[tuple[str, int]]] = {}  # (end, months) -> tables giving it
    for f in candidates:
        groups.setdefault((f.metric, f.period_end, f.months), []).append(f)
    out = []
    for (metric, end, months), cands in sorted(groups.items(), key=lambda g: g[0][0] != "revenue"):
        pool = cands
        if metric in ANCHORED:
            # net income and operating income from a table giving the period's revenue as read
            # (a segment's or a subsidiary's table gives another), else printed alike in two
            # tables or more (a release without a revenue line)
            rev = revenue.get((end, months), set())
            pool = [f for f in cands if (f.section_key, f.statement) in rev]
            if not pool and len({(f.section_key, f.statement) for f in cands}) > 1 \
                    and _agree(cands):
                pool = cands
            if not pool:
                read.leave("unanchored", len(cands))
                continue
        if metric == "net_income" and any(f.attributable for f in pool):
            pool = [f for f in pool if f.attributable]
        pool = [f for f in pool if f.titled] or pool
        if len({f.currency for f in pool}) > 1 or not _agree(pool):
            read.leave("ambiguous", len(cands))
            continue
        chosen = min(pool, key=lambda f: (f.precision, not f.titled, f.section_key, f.table_no,
                                          f.row_no, f.col_no))
        out.append(chosen)
        if metric == "revenue":
            revenue[(end, months)] = {(f.section_key, f.statement) for f in cands
                                      if _agree([chosen, f])}
    return sorted(out, key=lambda f: (f.metric, f.period_end, f.months or 0))


def read(tables: Iterable[tuple[str, int, Sequence[dict], str]], period: date,
         fye_month: int = 12) -> Read:
    """Every table of one release, in order: (section key, table number, rows, the text before
    the table)."""
    r = Read()
    tables = list(tables)
    period = r.period = own_period(tables, period, fye_month)
    candidates: list[Figure] = []
    previous: tuple[str, int, Sequence[dict]] | None = None
    statement = 0
    for key, n, rows, before in tables:
        if not (previous and previous[0] == key and _continues(previous[2], before)):
            statement = n
        candidates += read_table(rows, before, period, fye_month, section_key=key, table_no=n,
                                 statement=statement, read=r)
        previous = (key, n, rows)
    r.figures = choose(candidates, r)
    return r


_CONTINUED = re.compile(r"\((?:continued|concluded|cont'?d\.?)\)|^continued$", re.I)


def _continues(rows: Sequence[dict], before: str) -> bool:
    """Rule 8: a table continues the one before it in its section when that one ends with
    "(Continued)" or a line just before it says "(Continued)" / "(Concluded)"."""
    last = next((c for row in reversed(rows) for c in row.get("cells", []) if c.strip()), "")
    lines = [x for x in before.split("\n") if x.strip()][-TITLE_LINES:]
    return bool(_CONTINUED.search(last.strip())) or any(_CONTINUED.search(x.strip())
                                                         for x in lines)


def own_period(tables: Sequence[tuple[str, int, Sequence[dict], str]], period: date,
               fye_month: int) -> date:
    """Rule 3: the release's period. Its period of report, unless no table of figures states a
    period ending within 7 days of it (a 6-K dated by its filing): then the latest period those
    tables state ending on or before it, within 135 days."""
    ends: set[date] = set()
    for _, _, rows, _ in tables:
        grid = [_cells(r) for r in rows]
        labels = _labels(grid, rows)
        if not any(lab and line_of(key_of(lab[1]), "", None)[0] for lab in labels):
            continue
        first = next((i for i, lab in enumerate(labels) if lab is not None), None)
        if first is None:
            continue
        for p in _periods(_head(grid, rows, first), fye_month):
            ends.update(p.ends)
    if any(abs((e - period).days) <= CURRENT_DAYS for e in ends):
        return period
    earlier = [e for e in ends if e <= period and (period - e).days <= 135]
    return max(earlier) if earlier else period


def period_start(end: date, months: int | None) -> date | None:
    """The first day of a period `months` long ending on `end` (None for an instant): a month
    end's period starts on a month's first day ("three months ended March 31" from January 1),
    another's the day after the same day `months` months before."""
    if months is None:
        return None
    k = end.year * 12 + end.month - 1 - months  # zero-based month index `months` months before
    y, m = divmod(k, 12)
    if (end + timedelta(days=1)).day == 1:
        return _month_end(y, m + 1) + timedelta(days=1)
    day = min(end.day, _month_end(y, m + 1).day)
    return date(y, m + 1, day) + timedelta(days=1)


def fye_month(fiscal_year_end: str | None) -> int:
    """silver.companies.fiscal_year_end ('MMDD') as the month a fiscal year ends in; a 52-53 week
    year ending in a month's first week ends the month before; December when unknown."""
    if fiscal_year_end and re.match(r"^(0[1-9]|1[0-2])\d{2}$", fiscal_year_end):
        m, d = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
        return (m - 2) % 12 + 1 if d <= 7 else m
    return 12
