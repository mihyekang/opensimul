"""
agent_tools.py 단위 테스트.
execute_tool()이 올바른 db 함수를 호출하고 결과를 정리해 반환하는지 검증.
픽스처: db_mock (conftest.py)
"""
from datetime import date
import pytest
from unittest.mock import patch, MagicMock

from agent_tools import execute_tool, TOOLS


# ── query_spending_by_period ──────────────────────────────────────────────────

class TestQuerySpendingByPeriod:
    _receipts = [
        {"id": 1, "merchant": "이마트", "purchase_date": date(2026, 5, 1),
         "total": 10000, "item_count": 3},
        {"id": 2, "merchant": "쿠팡", "purchase_date": date(2026, 5, 2),
         "total": 5000, "item_count": 1},
    ]

    def test_sums_totals_correctly(self, db_mock):
        with patch("db.list_grocery_receipts", return_value=self._receipts):
            result = execute_tool("query_spending_by_period",
                                  {"start_date": "2026-05-01", "end_date": "2026-05-31"},
                                  "user1")
        assert result["total_spent"] == 15000
        assert result["receipt_count"] == 2

    def test_receipt_fields_present(self, db_mock):
        with patch("db.list_grocery_receipts", return_value=self._receipts):
            result = execute_tool("query_spending_by_period",
                                  {"start_date": "2026-05-01", "end_date": "2026-05-31"},
                                  "user1")
        first = result["receipts"][0]
        assert "id" in first and "date" in first and "merchant" in first

    def test_empty_period_returns_zero(self, db_mock):
        with patch("db.list_grocery_receipts", return_value=[]):
            result = execute_tool("query_spending_by_period",
                                  {"start_date": "2026-01-01", "end_date": "2026-01-31"},
                                  "user1")
        assert result["receipt_count"] == 0
        assert result["total_spent"] == 0


# ── query_spending_by_merchant ────────────────────────────────────────────────

class TestQuerySpendingByMerchant:
    def test_merchant_found(self, db_mock):
        rows = [{"id": 1, "merchant": "이마트", "purchase_date": date(2026, 5, 1), "total": 8000}]
        with patch("db.search_receipts_by_merchant", return_value=rows):
            result = execute_tool("query_spending_by_merchant",
                                  {"merchant": "이마트", "days": 90},
                                  "user1")
        assert result["receipt_count"] == 1
        assert result["total_spent"] == 8000

    def test_merchant_not_found(self, db_mock):
        with patch("db.search_receipts_by_merchant", return_value=[]):
            result = execute_tool("query_spending_by_merchant",
                                  {"merchant": "없는마트"}, "user1")
        assert result["receipt_count"] == 0


# ── search_items ──────────────────────────────────────────────────────────────

class TestSearchItems:
    def test_keyword_match_returns_items(self, db_mock):
        rows = [{"raw_name": "계란", "qty": 10, "amount": 3000,
                 "merchant": "이마트", "purchase_date": date(2026, 5, 1)}]
        with patch("db.search_grocery_items", return_value=rows):
            result = execute_tool("search_items", {"keyword": "계란"}, "user1")
        assert result["match_count"] == 1
        assert result["items"][0]["name"] == "계란"

    def test_no_match_returns_empty(self, db_mock):
        with patch("db.search_grocery_items", return_value=[]):
            result = execute_tool("search_items", {"keyword": "없는품목"}, "user1")
        assert result["match_count"] == 0


# ── get_spending_summary ──────────────────────────────────────────────────────

class TestGetSpendingSummary:
    def test_sorted_by_total_descending(self, db_mock):
        rows = [
            {"id": 1, "merchant": "쿠팡", "purchase_date": date(2026, 5, 1), "total": 5000, "item_count": 1},
            {"id": 2, "merchant": "이마트", "purchase_date": date(2026, 5, 2), "total": 15000, "item_count": 3},
        ]
        with patch("db.list_grocery_receipts", return_value=rows):
            result = execute_tool("get_spending_summary", {}, "user1")
        assert result["by_merchant"][0]["merchant"] == "이마트"
        assert result["total_spent"] == 20000

    def test_uses_default_date_range_when_omitted(self, db_mock):
        with patch("db.list_grocery_receipts", return_value=[]) as mock_list:
            execute_tool("get_spending_summary", {}, "user1")
        called_kwargs = mock_list.call_args
        assert called_kwargs is not None


# ── update_pantry_current_qty ─────────────────────────────────────────────────

class TestUpdatePantryCurrentQty:
    def test_delegates_to_deduct_pantry_qty(self, db_mock):
        expected = {"updated": True, "items": [{"name": "우유", "total_qty": 5,
                                                 "current_qty": 3, "unit": "개"}]}
        with patch("db.deduct_pantry_qty", return_value=expected) as mock_deduct:
            result = execute_tool("update_pantry_current_qty",
                                  {"item_name": "우유", "qty_used": 2}, "user1")
        mock_deduct.assert_called_once_with("user1", "우유", 2)
        assert result["updated"] is True


# ── get_pantry_items ──────────────────────────────────────────────────────────

class TestGetPantryItems:
    _items = [
        {"raw_name": "계란", "total_qty": 10, "current_qty": 7, "unit": "개", "updated_at": ""},
        {"raw_name": "우유", "total_qty": 2, "current_qty": 2, "unit": "개", "updated_at": ""},
        {"raw_name": "두부", "total_qty": 1, "current_qty": 1, "unit": "개", "updated_at": ""},
    ]

    def test_keyword_filters_items(self, db_mock):
        with patch("db.list_pantry", return_value=self._items):
            result = execute_tool("get_pantry_items", {"keyword": "계란"}, "user1")
        assert result["item_count"] == 1
        assert result["items"][0]["name"] == "계란"

    def test_empty_keyword_returns_all(self, db_mock):
        with patch("db.list_pantry", return_value=self._items):
            result = execute_tool("get_pantry_items", {}, "user1")
        assert result["item_count"] == 3
        assert result["keyword"] == "(전체)"


# ── 오류 처리 ─────────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_unknown_tool_name_returns_error_key(self, db_mock):
        result = execute_tool("nonexistent_tool", {}, "user1")
        assert "error" in result

    def test_db_exception_returns_error_not_raises(self, db_mock):
        with patch("db.list_grocery_receipts", side_effect=Exception("DB 연결 끊김")):
            result = execute_tool("query_spending_by_period",
                                  {"start_date": "2026-01-01", "end_date": "2026-01-31"},
                                  "user1")
        assert "error" in result

    def test_tools_list_is_nonempty(self):
        """TOOLS 목록이 정의되어 있는지 기본 확인."""
        assert len(TOOLS) > 0
        for t in TOOLS:
            assert t["type"] == "function"
            assert "name" in t["function"]
