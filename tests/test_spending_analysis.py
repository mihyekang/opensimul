"""analyze-monthly 엔드포인트 단위 테스트."""
import json
import pytest
from unittest.mock import patch, MagicMock


ITEMS_FEB = [
    {
        "raw_name": "사과", "amount": 3000, "purchase_date": "2024-02-10",
        "merchant": "마트A", "receipt_id": 1, "qty": 1, "unit_price": 3000,
    },
    {
        "raw_name": "우유", "amount": 2000, "purchase_date": "2024-02-15",
        "merchant": "마트A", "receipt_id": 2, "qty": 1, "unit_price": 2000,
    },
]

ITEMS_JAN = [
    {
        "raw_name": "계란", "amount": 5000, "purchase_date": "2024-01-15",
        "merchant": "마트A", "receipt_id": 3, "qty": 1, "unit_price": 5000,
    },
]

AI_ONE_MONTH = json.dumps({
    "summary": "2월 신선식품 위주 소비",
    "weeks": [
        {
            "label": "이번 달", "period": "2024-02", "total": 5000,
            "categories": {
                "신선식품": 5000, "가공식품": 0, "음료/주류": 0,
                "생활용품": 0, "반려동물용품": 0, "기타": 0,
            },
        }
    ],
})

AI_TWO_MONTHS = json.dumps({
    "summary": "2월이 1월보다 소비 감소",
    "weeks": [
        {
            "label": "이번 달", "period": "2024-02", "total": 5000,
            "categories": {
                "신선식품": 5000, "가공식품": 0, "음료/주류": 0,
                "생활용품": 0, "반려동물용품": 0, "기타": 0,
            },
        },
        {
            "label": "지난 달", "period": "2024-01", "total": 5000,
            "categories": {
                "신선식품": 5000, "가공식품": 0, "음료/주류": 0,
                "생활용품": 0, "반려동물용품": 0, "기타": 0,
            },
        },
    ],
})


class TestAnalyzeMonthlyEmpty:
    def test_empty_items_returns_empty(self, authed_client):
        """DB에 아이템 없으면 AI 호출 없이 빈 응답 반환."""
        with patch("db.get_items_by_date_range", return_value=[]):
            resp = authed_client.post(
                "/grocery/analyze-monthly",
                json={"start_date": "2024-01-01", "end_date": "2024-02-28"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == ""
        assert data["weeks"] == []

    def test_db_disabled_returns_200_empty(self, authed_client):
        """DB 비활성화 상태 → 200 빈 응답."""
        import app as _app
        _app.state.db_enabled = False
        resp = authed_client.post(
            "/grocery/analyze-monthly",
            json={"start_date": "2024-01-01", "end_date": "2024-02-28"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == ""
        assert data["weeks"] == []

    def test_invalid_date_returns_400(self, authed_client):
        """날짜 형식 오류 → 400."""
        resp = authed_client.post(
            "/grocery/analyze-monthly",
            json={"start_date": "not-a-date", "end_date": "2024-02-28"},
        )
        assert resp.status_code == 400


class TestAnalyzeMonthlyOneMonth:
    def test_single_month_returns_one_week(self, authed_client):
        """한 달치 데이터 → weeks 길이 1, label '이번 달'."""
        mock_client = MagicMock()
        mock_client.chat.return_value = (AI_ONE_MONTH, {})

        with patch("db.get_items_by_date_range", return_value=ITEMS_FEB), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-monthly",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["weeks"]) == 1
        assert data["weeks"][0]["label"] == "이번 달"
        assert data["summary"] != ""

    def test_single_month_total_in_weeks(self, authed_client):
        """weeks[0].total 값이 AI 응답에서 온 값과 일치."""
        mock_client = MagicMock()
        mock_client.chat.return_value = (AI_ONE_MONTH, {})

        with patch("db.get_items_by_date_range", return_value=ITEMS_FEB), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-monthly",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        data = resp.json()
        assert data["weeks"][0]["total"] == 5000


class TestAnalyzeMonthlyTwoMonths:
    def test_two_months_returns_two_weeks(self, authed_client):
        """두 달치 데이터 → weeks 길이 2, 최신 달 먼저."""
        mock_client = MagicMock()
        mock_client.chat.return_value = (AI_TWO_MONTHS, {})

        with patch("db.get_items_by_date_range", return_value=ITEMS_FEB + ITEMS_JAN), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-monthly",
                json={"start_date": "2024-01-01", "end_date": "2024-02-28"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["weeks"]) == 2
        assert data["weeks"][0]["label"] == "이번 달"
        assert data["weeks"][1]["label"] == "지난 달"

    def test_ai_parse_failure_falls_back(self, authed_client):
        """AI가 유효하지 않은 JSON 반환 → summary는 raw 텍스트 일부, weeks=[]."""
        mock_client = MagicMock()
        mock_client.chat.return_value = ("분석 결과를 생성할 수 없습니다.", {})

        with patch("db.get_items_by_date_range", return_value=ITEMS_FEB), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post(
                "/grocery/analyze-monthly",
                json={"start_date": "2024-02-01", "end_date": "2024-02-28"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["weeks"] == []
        assert len(data["summary"]) > 0
