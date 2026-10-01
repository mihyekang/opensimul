"""Infer quantity and unit from grocery item names."""

import re

_NAMED = [
    ("한판",  30, "개"),
    ("반판",  15, "개"),
]

_PATTERNS = [
    (r"(\d+)구",    "개"),
    (r"(\d+)개입",  "개"),
    (r"(\d+)캔",    "캔"),
    (r"(\d+)병",    "병"),
    (r"(\d+)팩",    "팩"),
    (r"(\d+)봉지",  "봉"),
    (r"(\d+)봉",    "봉"),
    (r"(\d+)\s*[Ll](?!\w)", "L"),
    (r"(\d+)\s*ml", "ml"),
    (r"(\d+)\s*[Kk][Gg]", "kg"),
    (r"(\d+)\s*[Gg](?!\w)", "g"),
    (r"(\d+)개",    "개"),
]


def infer_qty(raw_name: str, receipt_qty: int = 1) -> tuple[int, str]:
    """Return (total_quantity, unit) inferred from item name and receipt qty."""
    name = raw_name or ""
    for keyword, qty, unit in _NAMED:
        if keyword in name:
            return qty * max(receipt_qty, 1), unit
    for pattern, unit in _PATTERNS:
        m = re.search(pattern, name)
        if m:
            return int(m.group(1)) * max(receipt_qty, 1), unit
    return max(receipt_qty, 1), "개"
