"""/grocery/extract 엔드포인트 단위 테스트 — 이미지 업로드 → Pass1 추출 + 검증."""
import io
from unittest.mock import patch, MagicMock

from PIL import Image


def _tiny_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (10, 10), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _upload(client, result: dict, raw: str = "{}"):
    mock_config = MagicMock()
    mock_config.verify_ssl = False
    with patch("app.ClientConfig", return_value=mock_config), \
         patch("app.extract_pass1_bytes", return_value=(result, raw)):
        return client.post(
            "/grocery/extract",
            files={"image": ("receipt.jpg", _tiny_jpeg_bytes(), "image/jpeg")},
        )


class TestGroceryExtract:
    def test_normal_extraction_returns_result_and_issues(self, client):
        result = {
            "merchant": "이마트",
            "purchase_date": "2024-02-10",
            "total": 5000,
            "items": [{"raw_name": "사과", "amount": 5000, "qty": 1}],
        }
        resp = _upload(client, result)

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["merchant"] == "이마트"
        assert "_date_inferred" not in data["result"]
        assert data["issues"] == []

    def test_missing_purchase_date_gets_inferred_today(self, client):
        result = {
            "merchant": "이마트",
            "purchase_date": None,
            "total": 5000,
            "items": [{"raw_name": "사과", "amount": 5000, "qty": 1}],
        }
        resp = _upload(client, result)

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["_date_inferred"] is True
        assert data["result"]["purchase_date"]
        assert any("오늘 날짜로 대체" in issue for issue in data["issues"])

    def test_missing_merchant_reported_as_issue(self, client):
        result = {
            "merchant": None,
            "purchase_date": "2024-02-10",
            "total": 5000,
            "items": [{"raw_name": "사과", "amount": 5000, "qty": 1}],
        }
        resp = _upload(client, result)

        assert resp.status_code == 200
        data = resp.json()
        assert any("merchant" in issue for issue in data["issues"])

    def test_empty_items_reported_as_issue(self, client):
        result = {
            "merchant": "이마트",
            "purchase_date": "2024-02-10",
            "total": 0,
            "items": [],
        }
        resp = _upload(client, result)

        assert resp.status_code == 200
        data = resp.json()
        assert any("items" in issue for issue in data["issues"])
