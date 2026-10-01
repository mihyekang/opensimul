"""/chat 엔드포인트 단위 테스트 — 도구 호출 경로 / 일반 채팅 경로."""
import types
from unittest.mock import patch, MagicMock

from azure_openai_client import TurnUsage


def _mock_client(reply: str, usage: TurnUsage):
    c = MagicMock()
    c.config = types.SimpleNamespace(input_price_per_m=0.15, output_price_per_m=0.60)
    c.chat.return_value = (reply, usage)
    c.chat_with_tools.return_value = (reply, usage)
    c.total_tokens = 100
    c.total_cost = 0.001
    return c


class TestChatWithTools:
    def test_authed_user_uses_tool_calling_path(self, authed_client):
        """db_enabled + user_id 존재 → chat_with_tools 경로 사용."""
        mock_client = _mock_client("안녕하세요!", TurnUsage(prompt_tokens=10, completion_tokens=20))

        with patch("db.get_recent_groceries", return_value=[]), \
             patch("app._client", return_value=mock_client):
            resp = authed_client.post("/chat", json={"message": "안녕", "session_id": "s1"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "안녕하세요!"
        assert data["usage"]["prompt_tokens"] == 10
        assert data["usage"]["completion_tokens"] == 20
        mock_client.chat_with_tools.assert_called_once()
        mock_client.chat.assert_not_called()


class TestChatAnonymousFallback:
    def test_no_user_id_uses_plain_chat_path(self, client):
        """db_enabled=True지만 쿠키 없는 익명 요청 → user_id="" → 일반 chat() 경로."""
        mock_client = _mock_client("일반 응답", TurnUsage(prompt_tokens=5, completion_tokens=8))

        with patch("app._client", return_value=mock_client):
            resp = client.post("/chat", json={"message": "안녕", "session_id": "s1"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "일반 응답"
        mock_client.chat.assert_called_once()
        mock_client.chat_with_tools.assert_not_called()

    def test_db_disabled_uses_plain_chat_path(self, reset_app_state):
        """db_enabled=False 상태(쿠키 없는 익명 요청)에서도 /chat은 정상 동작해야 함."""
        from app import app
        from fastapi.testclient import TestClient

        mock_config = MagicMock()
        mock_config.verify_ssl = False
        mock_client = _mock_client("DB 없이도 응답", TurnUsage(prompt_tokens=3, completion_tokens=4))

        with patch("app.ClientConfig", return_value=mock_config), \
             patch("app.fetch_usd_to_krw", return_value=(1300.0, "test")), \
             patch("app._client", return_value=mock_client):
            with TestClient(app) as c:
                resp = c.post("/chat", json={"message": "안녕", "session_id": "s1"})

        assert resp.status_code == 200
        assert resp.json()["reply"] == "DB 없이도 응답"
        mock_client.chat.assert_called_once()
