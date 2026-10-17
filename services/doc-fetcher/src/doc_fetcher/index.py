"""Parse an EDGAR filing index page (…-index.htm)."""

from dataclasses import dataclass
from datetime import date

from lxml import html

SEC_BASE = "https://www.sec.gov"


@dataclass(frozen=True)
class FilingIndex:
    filed: date
    period_of_report: date | None
    document_name: str
    url: str


def parse_index(raw: bytes, form: str) -> FilingIndex:
    """The primary document is the row whose Type equals the form (sequence 1 in practice)."""
    doc = html.fromstring(raw)
    info = _form_info(doc)
    for tr in doc.xpath("//table[@class='tableFile']//tr[td]"):
        cells = [c.text_content().strip() for c in tr.xpath("./td")]
        hrefs = tr.xpath(".//a/@href")
        if len(cells) >= 4 and cells[3] == form and hrefs:
            href = hrefs[0].removeprefix("/ix?doc=")  # inline-XBRL viewer link
            return FilingIndex(
                filed=date.fromisoformat(info["Filing Date"]),
                period_of_report=(date.fromisoformat(info["Period of Report"])
                                  if "Period of Report" in info else None),
                document_name=href.rsplit("/", 1)[-1],
                url=SEC_BASE + href,
            )
    raise ValueError(f"no {form} document in filing index")


def _form_info(doc: html.HtmlElement) -> dict[str, str]:
    """The 'Filing Date', 'Period of Report', ... label/value pairs at the top of the page."""
    info = {}
    for group in doc.xpath("//div[@class='formGrouping']"):
        labels = [e.text_content().strip() for e in group.xpath("./div[@class='infoHead']")]
        values = [e.text_content().strip() for e in group.xpath("./div[@class='info']")]
        info.update(zip(labels, values, strict=False))
    return info
