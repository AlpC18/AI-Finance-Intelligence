"""CSV rendering for exports, hardened against spreadsheet formula injection.

A CSV is not an inert format. Excel, LibreOffice and Sheets evaluate any cell
whose text begins with `=`, `+`, `-`, `@`, or a leading tab/CR, so a value that
travelled through this system as data becomes code the moment an accountant
opens the file. The audit trail carries up to 4000 characters of free-form
model reasoning, which makes that a live path here rather than a theoretical
one.

Mitigation is the OWASP-recommended one: prefix a risky cell with a single
quote so the spreadsheet treats it as text. The quote is visible in the cell,
which is the accepted cost - a visible quote beats a silently executed formula.
"""
from __future__ import annotations

from decimal import Decimal

import csv
import io
from typing import Any, Iterable, Sequence

# Leading characters that make a spreadsheet treat a cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: Any) -> str:
    """Render one value as CSV-safe text.

    Numbers pass through untouched: a negative number legitimately starts with
    `-`, and quoting it would corrupt the figure for every downstream reader.
    Only strings can carry a formula, so only strings are guarded.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Decimal):
        # Render at natural scale: a quantity stored as NUMERIC(28,8) is
        # `3.00000000`, and eight trailing zeros in every cell is noise a
        # spreadsheet reader has to strip back off.
        normalized = value.normalize()
        _, _, exponent = normalized.as_tuple()
        if isinstance(exponent, int) and exponent > 0:  # 1E+2 -> 100
            normalized = normalized.quantize(Decimal(1))
        return format(normalized, "f")
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def to_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Render a full CSV document. Every cell goes through `sanitize_cell`."""
    buf = io.StringIO()
    # QUOTE_MINIMAL plus \r\n: RFC 4180, which is what spreadsheet apps expect.
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(list(headers))
    for row in rows:
        writer.writerow([sanitize_cell(cell) for cell in row])
    return buf.getvalue()


def content_disposition(filename: str) -> str:
    """An attachment header whose filename cannot break out of the quoting."""
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.")
    return f'attachment; filename="{safe}"'
