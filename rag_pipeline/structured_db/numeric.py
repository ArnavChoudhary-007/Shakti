from __future__ import annotations

import math
import re
from typing import Any, Optional


def as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        cleaned = re.sub(r"[,\s$\u20b9\u20ac\u00a3]", "", value)
        if re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
            number = float(cleaned)
            return number if math.isfinite(number) else None
    return None


def is_identifier(column: str) -> bool:
    return bool(re.search(r"(^|_)(id|index|year|number|no)$", column.lower()))


def format_summary(column: str, total: float, count: int, average: float, minimum: float, maximum: float) -> str:
    return (
        f"- {column}: total {total:,.2f} across {count} row(s); "
        f"average {average:,.2f}; min {minimum:,.2f}; max {maximum:,.2f}"
    )
