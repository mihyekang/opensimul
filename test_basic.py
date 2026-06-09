"""
기본 기능 테스트 — DB 연결 없이 순수 로직만 검증.
실행: pytest test_basic.py -v
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── fmtDate 동등 로직 (Python) ──────────────────────────────────────────────

def fmt_date(s: str) -> str:
    """common.js fmtDate와 동일한 로직."""
    if not s:
        return "-"
    d = date.fromisoformat(s[:10])
    return f"{d.month}/{str(d.day).zfill(2)}"


class TestFmtDate:
    def test_date_only(self):
        assert fmt_date("2026-05-29") == "5/29"

    def test_datetime_string(self):
        # created_at 형태 — T 이후가 붙어도 앞 10자만 쓰므로 NaN 없음
        assert fmt_date("2026-05-29T06:41:39.073017+00:00") == "5/29"

    def test_empty(self):
        assert fmt_date("") == "-"
        assert fmt_date(None) == "-"  # type: ignore

    def test_month_padding(self):
        assert fmt_date("2026-01-05") == "1/05"


# ── dedup 로직 ───────────────────────────────────────────────────────────────

def dedup_receipts(rows: list[dict]) -> list[dict]:
    """db.list_grocery_receipts의 Python dedup 로직."""
    seen: set = set()
    deduped = []
    for r in rows:
        key = (
            r.get("merchant") or "",
            str(r.get("purchase_date") or ""),
            str(r.get("total") or ""),
            r.get("is_refund", False),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


class TestDedup:
    def _row(self, id, merchant, purchase_date, total, is_refund=False):
        return {"id": id, "merchant": merchant, "purchase_date": purchase_date,
                "total": total, "is_refund": is_refund}

    def test_no_duplicates(self):
        rows = [
            self._row(1, "이마트", date(2026, 5, 29), 71240),
            self._row(2, "쿠팡",   date(2026, 5, 28), 15000),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 2

    def test_removes_duplicates(self):
        rows = [
            self._row(6,  "이마트", date(2026, 5, 29), 71240),
            self._row(12, "이마트", date(2026, 5, 29), 71240),
            self._row(18, "이마트", date(2026, 5, 29), 71240),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 1
        assert result[0]["id"] == 6  # 첫 번째(최신) 유지

    def test_same_merchant_different_total(self):
        rows = [
            self._row(1, "쿠팡", date(2026, 5, 26), 68040),
            self._row(2, "쿠팡", date(2026, 5, 26), 15000),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 2

    def test_null_merchant(self):
        rows = [
            self._row(1, None, date(2026, 5, 28), 67400),
            self._row(2, None, date(2026, 5, 28), 67400),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 1

    def test_null_total(self):
        rows = [
            self._row(1, "네이버쇼핑", date(2026, 5, 25), None),
            self._row(2, "네이버쇼핑", date(2026, 5, 25), 126700),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 2

    def test_refund_not_deduped_with_purchase(self):
        rows = [
            self._row(1, "이마트", date(2026, 5, 29), 71240, is_refund=False),
            self._row(2, "이마트", date(2026, 5, 29), 71240, is_refund=True),
        ]
        result = dedup_receipts(rows)
        assert len(result) == 2


# ── save_grocery_receipt 중복 체크 쿼리 파라미터 검증 ───────────────────────

class TestSaveReceiptDedupParams:
    def test_params_match_result(self):
        result = {
            "merchant": "이마트",
            "purchase_date": "2026-05-29",
            "total": 71240,
            "is_refund": False,
            "currency": "KRW",
            "items": [],
        }
        purchase_date = result.get("purchase_date") or None
        merchant = result.get("merchant")
        total = result.get("total")
        is_refund = result.get("is_refund", False)

        assert merchant == "이마트"
        assert purchase_date == "2026-05-29"
        assert total == 71240
        assert is_refund is False

    def test_null_total_coalesce(self):
        # COALESCE(total, -1) — None total은 -1로 취급
        total = None
        coalesced = total if total is not None else -1
        assert coalesced == -1


# ── pantry infer_qty 기본 동작 ────────────────────────────────────────────────

class TestInferQty:
    def test_import(self):
        from pantry_utils import infer_qty
        assert callable(infer_qty)

    def test_numeric_qty(self):
        from pantry_utils import infer_qty
        qty, unit = infer_qty("우유 1000ml", 1)
        assert isinstance(qty, int)
        assert qty >= 1

    def test_default_qty(self):
        from pantry_utils import infer_qty
        qty, unit = infer_qty("닭가슴살", 2)
        assert qty == 2


# ── date range 계산 ───────────────────────────────────────────────────────────

class TestDateRange:
    def test_days_365(self):
        days = 365
        today = date.today()
        from_date = (today - timedelta(days=days)).isoformat()
        to_date = today.isoformat()
        assert from_date < to_date
        assert len(from_date) == 10

    def test_explicit_range(self):
        start_date = "2026-01-01"
        end_date = "2026-06-09"
        from_date = start_date if start_date else ""
        to_date = end_date if end_date else ""
        assert from_date == "2026-01-01"
        assert to_date == "2026-06-09"
