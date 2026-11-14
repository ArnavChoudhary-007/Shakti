"""
structured_db/dates.py
Invoice dates arrive as free text ("January 15, 2024", "15/01/2024",
"2024-01-15"). Comparing those strings gives wrong answers ("April" < "March"),
so dates are stored and compared as ISO `YYYY-MM-DD`.

Partial dates become ranges: "March 2024" is 2024-03-01 … 2024-03-31 and
"2024" is the whole year — `end=True` picks the last day.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from dateutil import parser as _parser

# Two different defaults reveal which parts the text actually contained.
# Both default months have 31 days, so a day of 31 parses either way.
_DEFAULT_A = datetime(1904, 1, 1)
_DEFAULT_B = datetime(1908, 3, 2)


def parse_date(text: Optional[str], *, end: bool = False, day_first: bool = False,
               fuzzy: bool = False) -> Optional[str]:
    """ISO date for `text`, or None if it isn't a date with a year."""
    if not text or not str(text).strip():
        return None
    text = str(text).strip()
    try:
        a = _parser.parse(text, default=_DEFAULT_A, dayfirst=day_first, fuzzy=fuzzy)
        b = _parser.parse(text, default=_DEFAULT_B, dayfirst=day_first, fuzzy=fuzzy)
    except (ValueError, OverflowError, TypeError):
        return None
    if a.year != b.year or not 1900 <= a.year <= 2100:
        return None                      # no (plausible) year in the text
    year = a.year
    month = a.month if a.month == b.month else None
    day = a.day if a.day == b.day else None
    if month is None:
        month, day = (12, 31) if end else (1, 1)
    elif day is None:
        day = calendar.monthrange(year, month)[1] if end else 1
    return date(year, month, day).isoformat()


# ── Date ranges in questions ──────────────────────────────────

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_YEAR = r"(?:19|20)\d{2}"
_ORD = r"(?:st|nd|rd|th)?"
DATE = (
    rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}"                      # 2024-01-15
    rf"|\d{{1,2}}[/.-]\d{{1,2}}[/.-](?:\d{{4}}|\d{{2}})"     # 15/01/2024, 1.15.24
    rf"|\d{{1,2}}{_ORD}\s+(?:of\s+)?{_MONTH},?\s+{_YEAR}"    # 15 January 2024
    rf"|{_MONTH}\s+\d{{1,2}}{_ORD},?\s+{_YEAR}"               # January 15, 2024
    rf"|{_MONTH},?\s+{_YEAR}"                                # January 2024
    rf"|{_YEAR})"                                            # 2024
)
_D = rf"\b({DATE})\b"
_RANGE = re.compile(rf"\b(?:between|from)\s+{_D}\s+(?:and|to|until|till|through|-)\s+{_D}", re.I)
_AFTER = re.compile(rf"\bafter\s+{_D}", re.I)
_SINCE = re.compile(rf"\b(?:since|from|starting)\s+{_D}", re.I)
_BEFORE = re.compile(rf"\bbefore\s+{_D}", re.I)
_UNTIL = re.compile(rf"\b(?:until|till|through|up\s+to|by)\s+{_D}", re.I)
_WITHIN = re.compile(rf"\b(?:in|during|for|of|on|dated)\s+{_D}", re.I)


def _shift(iso: Optional[str], days: int) -> Optional[str]:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat() if iso else None


def extract_date_range(query: str, *, day_first: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(date_from, date_to) as inclusive ISO dates found in a question, else (None, None)."""
    def start(text: str) -> Optional[str]:
        return parse_date(text, day_first=day_first)

    def stop(text: str) -> Optional[str]:
        return parse_date(text, end=True, day_first=day_first)

    m = _RANGE.search(query)
    if m:
        return start(m.group(1)), stop(m.group(2))

    date_from = date_to = None
    if (m := _AFTER.search(query)):
        date_from = _shift(stop(m.group(1)), 1)        # after March 2024 → from April 1
    elif (m := _SINCE.search(query)):
        date_from = start(m.group(1))
    if (m := _BEFORE.search(query)):
        date_to = _shift(start(m.group(1)), -1)         # before March 2024 → to Feb 29
    elif (m := _UNTIL.search(query)):
        date_to = stop(m.group(1))
    if date_from or date_to:
        return date_from, date_to

    if (m := _WITHIN.search(query)):                     # in March 2024 / during 2023 / on 5 Jan 2024
        return start(m.group(1)), stop(m.group(1))
    return None, None
