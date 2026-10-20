"""CSV responses. Numbers are written exactly as the database returns them (decimal strings);
text that a spreadsheet would run as a formula is prefixed with a quote."""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal

from fastapi.responses import Response

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal | int | float):
        return str(v)
    if isinstance(v, date | datetime):
        return v.isoformat()
    if isinstance(v, list | tuple | set):
        return " ".join(cell(x) for x in v)
    s = str(v)
    return "'" + s if s.startswith(_FORMULA_START) else s


def csv_response(filename: str, header: Sequence[str],
                 rows: Iterable[Sequence[object]]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([cell(v) for v in r])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
