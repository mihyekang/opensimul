"""analyze-purchases 엔드포인트(/grocery/analyze-purchases) 단위 테스트."""
import json
from unittest.mock import patch, MagicMock


ITEMS = [
    {"raw_name": "사과", "amount": 3000, "purchase_date": "2024-02-10",
     "merchant": "마트A", "receipt_id": 1, "qty": 1, "unit_price": 3000},
    {"raw_name": "콜라", "amount": 2000, "purchase_date": "2024-02-11",
     "merchant": "마트A", "receipt_id": 2, "qty": 1, "unit_price": 2000},
]

AI_REPLY = json.dumps({
    "summary": "신선식품과 음료 위주로 5,000원 소비",
    "categories": {
        "신선식품": ["사과"],
        "가공식품": [],
        "음료/주류": ["콜라"],
        "생활용품": [],
        "반려동물용품": [],
        "기타": [],
    },
})


class TestAnalyzePurchasesEmpty:
    def test_empty_items_returns_empty(self, authed_client):
        with patch("db.get_items_by_date_range", return_value=[]):
            resp = authed_client.post(
                "/grocery/analyze-purchases",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == ""
        assert data["categories"] == []
        assert data["items"] == []

    def test_db_disabled_returns_200_empty(self, authed_client):
        import app as _app
        _app.state.db_enabled = False
        resp = authed_client.post(
            "/grocery/analyze-purchases",
            json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == ""
        assert data["categories"] == []

    def test_invalid_date_returns_400(self, authed_client):
        resp = authed_client.post(
            "/grocery/analyze-purchases",
            json={"start_date": "not-a-date", "end_date": "2024-02-28"},
        )
        assert resp.status_code == 400


class TestAnalyzePurchasesWithItems:
    def test_categories_grouped_with_amounts(self, authed_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = (AI_REPLY, {})

        with patch("db.get_items_by_date_range", return_value=ITEMS), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-purchases",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] != ""
        cats = {c["name"]: c["amount"] for c in data["categories"]}
        assert cats["신선식품"] == 3000
        assert cats["음료/주류"] == 2000
        assert len(data["items"]) == 2

    def test_categories_sorted_by_amount_desc(self, authed_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = (AI_REPLY, {})

        with patch("db.get_items_by_date_range", return_value=ITEMS), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-purchases",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        data = resp.json()
        amounts = [c["amount"] for c in data["categories"]]
        assert amounts == sorted(amounts, reverse=True)

    def test_ai_parse_failure_falls_back_to_empty_categories(self, authed_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = ("분석할 수 없습니다.", {})

        with patch("db.get_items_by_date_range", return_value=ITEMS), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-purchases",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == ""
        assert data["categories"] == []
        assert len(data["items"]) == 2
