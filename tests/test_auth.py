"""
인증 엔드포인트 및 RateLimiter 테스트.
픽스처: client (DB 활성화, 비로그인), authed_client (user_code='testuser')
DB 함수는 unittest.mock.patch로 대체.
"""
import time
import pytest
from unittest.mock import patch


# ── DB 비활성화 폴백 ──────────────────────────────────────────────────────────

class TestLoginNoDb:
    def test_db_disabled_sets_cookie_to_user_code(self, reset_app_state):
        """DB 없으면 user_code 값을 sid 쿠키로 직접 세팅."""
        import app as _app
        from app import app
        from fastapi.testclient import TestClient
        from unittest.mock import patch, MagicMock

        mock_config = MagicMock()
        mock_config.verify_ssl = False
        with patch("app.ClientConfig", return_value=mock_config), \
             patch("app.fetch_usd_to_krw", return_value=(1300.0, "test")):
            with TestClient(app) as c:
                # lifespan 후 db_enabled는 False (환경변수 없으므로) — 원하는 상태 그대로
                resp = c.post("/auth/login", json={"user_code": "alice", "password": "any"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["user_code"] == "alice"
        assert "sid" in resp.cookies


# ── 로그인 ────────────────────────────────────────────────────────────────────

class TestLogin:
    def test_success_returns_ok_and_sets_cookie(self, client):
        with patch("db.login_user", return_value=True), \
             patch("db.create_user_session", return_value="tok_abc"):
            resp = client.post("/auth/login", json={"user_code": "alice", "password": "TestPass1"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "user_code": "alice"}
        assert resp.cookies.get("sid") == "tok_abc"

    def test_wrong_password_returns_invalid(self, client):
        with patch("db.login_user", return_value=False):
            resp = client.post("/auth/login", json={"user_code": "alice", "password": "wrong"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["reason"] == "invalid"

    def test_unknown_user_returns_invalid(self, client):
        with patch("db.login_user", return_value=False):
            resp = client.post("/auth/login", json={"user_code": "ghost", "password": "pw"})
        assert resp.json()["ok"] is False

    def test_missing_password_field_returns_422(self, client):
        resp = client.post("/auth/login", json={"user_code": "alice"})
        assert resp.status_code == 422


# ── Rate Limit ────────────────────────────────────────────────────────────────

class TestRateLimit:
    def test_blocks_after_max_attempts(self, client):
        """5회 실패 후 6번째 요청은 429."""
        with patch("db.login_user", return_value=False):
            for _ in range(5):
                client.post("/auth/login", json={"user_code": "bob", "password": "x"})
            resp = client.post("/auth/login", json={"user_code": "bob", "password": "x"})
        assert resp.status_code == 429
        assert resp.json()["detail"] == "too_many_attempts"

    def test_success_clears_failure_count(self, client):
        """4회 실패 후 성공하면 카운터 초기화 → 다음 요청 허용."""
        with patch("db.login_user", return_value=False):
            for _ in range(4):
                client.post("/auth/login", json={"user_code": "carol", "password": "x"})
        with patch("db.login_user", return_value=True), \
             patch("db.create_user_session", return_value="tok"):
            client.post("/auth/login", json={"user_code": "carol", "password": "Good1"})
        with patch("db.login_user", return_value=False):
            resp = client.post("/auth/login", json={"user_code": "carol", "password": "x"})
        assert resp.status_code == 200  # 429 아님

    def test_window_expiry_resets_counter(self):
        """윈도우 시간 경과 후 카운터 초기화."""
        from app import RateLimiter, _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS

        limiter = RateLimiter(max_attempts=_LOGIN_MAX_ATTEMPTS, window_seconds=_LOGIN_WINDOW_SECONDS)
        old_time = time.time() - (_LOGIN_WINDOW_SECONDS + 10)
        limiter._attempts["dave"] = [old_time] * 5

        assert limiter.is_allowed("dave") is True

    def test_per_user_isolation(self, client):
        """한 유저의 실패가 다른 유저에게 영향 없음."""
        with patch("db.login_user", return_value=False):
            for _ in range(5):
                client.post("/auth/login", json={"user_code": "blocked", "password": "x"})
            resp = client.post("/auth/login", json={"user_code": "other", "password": "x"})
        assert resp.status_code == 200


# ── 회원가입 ──────────────────────────────────────────────────────────────────

class TestRegister:
    def test_success(self, client):
        with patch("db.register_user", return_value=True), \
             patch("db.create_user_session", return_value="tok_new"):
            resp = client.post("/auth/register",
                               json={"user_code": "newuser", "password": "TestPass1"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_duplicate_user_code(self, client):
        with patch("db.register_user", return_value=False):
            resp = client.post("/auth/register",
                               json={"user_code": "existing", "password": "TestPass1"})
        assert resp.json()["ok"] is False
        assert resp.json()["reason"] == "already_exists"

    def test_weak_password_no_uppercase_returns_422(self, client):
        resp = client.post("/auth/register",
                           json={"user_code": "u", "password": "nouppercase1"})
        assert resp.status_code == 422

    def test_weak_password_too_short_returns_422(self, client):
        resp = client.post("/auth/register",
                           json={"user_code": "u", "password": "Ab1"})
        assert resp.status_code == 422


# ── 로그아웃 ──────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_returns_ok(self, authed_client):
        with patch("db.delete_user_session"):
            resp = authed_client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_logout_calls_delete_session_with_sid(self, authed_client):
        with patch("db.delete_user_session") as mock_del:
            authed_client.cookies.set("sid", "my_session_token")
            authed_client.post("/auth/logout")
        mock_del.assert_called_once_with("my_session_token")


# ── 세션 확인 ─────────────────────────────────────────────────────────────────

class TestSessionCheck:
    def test_authed_returns_user_id(self, authed_client):
        resp = authed_client.get("/auth/session")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "testuser"

    def test_no_sid_returns_empty_user_id(self, client):
        with patch("db.get_user_from_session", return_value=None):
            resp = client.get("/auth/session")
        assert resp.json()["user_id"] == ""
