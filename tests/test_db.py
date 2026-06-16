"""
db.py 핵심 함수 단위 테스트 — 실제 PostgreSQL 없이 MockConnection으로 실행.
픽스처: db_mock (conftest.py)
"""
import sys
import pytest
import psycopg2.errors
from unittest.mock import MagicMock

import db
from conftest import MockCursor, cursor_sequence


# ── 비밀번호 해싱 ─────────────────────────────────────────────────────────────

class TestHashPassword:
    def test_same_input_produces_different_hashes(self):
        """salt가 달라서 동일 비밀번호도 매번 다른 해시."""
        h1 = db._hash_password("TestPass1")
        h2 = db._hash_password("TestPass1")
        assert h1 != h2

    def test_new_format_has_three_parts(self):
        h = db._hash_password("TestPass1")
        parts = h.split(":")
        assert len(parts) == 3
        assert parts[0] == str(db._PBKDF2_ITERATIONS)

    def test_verify_correct_password(self):
        h = db._hash_password("TestPass1")
        assert db._verify_password("TestPass1", h) is True

    def test_verify_wrong_password(self):
        h = db._hash_password("TestPass1")
        assert db._verify_password("WrongPass1", h) is False

    def test_verify_legacy_format(self):
        """구버전(100k, 2파트) 해시도 검증 가능."""
        import hashlib, secrets
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", b"OldPass1", salt.encode(), 100_000)
        stored = f"{salt}:{h.hex()}"
        assert db._verify_password("OldPass1", stored) is True
        assert db._verify_password("WrongPass", stored) is False


# ── 사용자 로그인 ─────────────────────────────────────────────────────────────

class TestLoginUser:
    def test_correct_password_returns_true(self, db_mock):
        pw_hash = db._hash_password("TestPass1")
        db_mock.cursor = MagicMock(side_effect=[
            _dict_cursor(fetchone={"password_hash": pw_hash}),
        ])
        assert db.login_user("alice", "TestPass1") is True

    def test_wrong_password_returns_false(self, db_mock):
        pw_hash = db._hash_password("TestPass1")
        db_mock.cursor = MagicMock(side_effect=[
            _dict_cursor(fetchone={"password_hash": pw_hash}),
        ])
        assert db.login_user("alice", "WrongPass1") is False

    def test_missing_user_returns_false(self, db_mock):
        db_mock.cursor = MagicMock(side_effect=[
            _dict_cursor(fetchone=None),
        ])
        assert db.login_user("ghost", "any") is False


# ── 사용자 등록 ───────────────────────────────────────────────────────────────

class TestRegisterUser:
    def test_success_returns_true_and_commits(self, db_mock):
        result = db.register_user("newuser", "TestPass1")
        assert result is True
        db_mock.commit.assert_called_once()

    def test_duplicate_returns_false(self, db_mock):
        db_mock.cursor = MagicMock(side_effect=[
            _raising_cursor(psycopg2.errors.UniqueViolation),
        ])
        result = db.register_user("existing", "TestPass1")
        assert result is False


# ── 세션 생성/조회 ────────────────────────────────────────────────────────────

class TestCreateUserSession:
    def test_returns_64char_hex_token(self, db_mock):
        token = db.create_user_session("alice")
        assert isinstance(token, str)
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_commits_after_insert(self, db_mock):
        db.create_user_session("alice")
        db_mock.commit.assert_called_once()

    def test_deletes_excess_sessions_before_insert(self, db_mock):
        db.create_user_session("alice")
        cur = db_mock.last_cursor
        calls = [str(c.args[0]).strip() for c in cur.execute.call_args_list]
        assert any("DELETE" in s for s in calls)
        assert any("INSERT" in s for s in calls)


class TestGetUserFromSession:
    def test_valid_token_returns_user_code(self, db_mock):
        db_mock.cursor = MagicMock(side_effect=[
            _plain_cursor(fetchone=("alice",)),
        ])
        assert db.get_user_from_session("valid_token") == "alice"

    def test_expired_token_returns_none(self, db_mock):
        db_mock.cursor = MagicMock(side_effect=[
            _plain_cursor(fetchone=None),
        ])
        assert db.get_user_from_session("old_token") is None


# ── 영수증 저장 ───────────────────────────────────────────────────────────────

class TestSaveGroceryReceipt:
    _base_result = {
        "merchant": "이마트",
        "purchase_date": "2026-05-01",
        "total": 10000,
        "is_refund": False,
        "currency": "KRW",
        "items": [],
    }

    def test_new_receipt_inserts_and_returns_id(self, db_mock):
        # save_grocery_receipt은 단일 cursor에서 SELECT → INSERT 순서로 fetchone 두 번 호출
        cur = MockCursor()
        cur.fetchone.side_effect = [None, (42,)]   # 1번: 중복 없음, 2번: 새 id
        db_mock.cursor = MagicMock(return_value=cur)
        result = db.save_grocery_receipt(self._base_result, user_id="u1")
        assert result == 42
        db_mock.commit.assert_called_once()

    def test_duplicate_returns_existing_id(self, db_mock):
        cur = MockCursor()
        cur.fetchone.return_value = (7,)            # SELECT → 이미 존재
        db_mock.cursor = MagicMock(return_value=cur)
        result = db.save_grocery_receipt(self._base_result, user_id="u1")
        assert result == 7

    def test_items_are_inserted(self, db_mock):
        result = dict(self._base_result)
        result["items"] = [
            {"raw_name": "우유", "qty": 1, "unit_price": 1500, "amount": 1500, "is_cancelled": False},
            {"raw_name": "계란", "qty": 10, "unit_price": 300, "amount": 3000, "is_cancelled": False},
        ]
        cur = MockCursor()
        cur.fetchone.side_effect = [None, (99,)]   # 중복 없음, 새 id
        db_mock.cursor = MagicMock(return_value=cur)
        db.save_grocery_receipt(result, user_id="u1")
        # SELECT(1) + INSERT receipt(1) + INSERT item×2(2) = execute 4회
        assert cur.execute.call_count == 4


# ── 팬트리 삭제 ───────────────────────────────────────────────────────────────

class TestDeletePantryItem:
    def test_existing_item_returns_true(self, db_mock):
        cur = MockCursor()
        cur.rowcount = 1
        db_mock.cursor = MagicMock(return_value=cur)
        assert db.delete_pantry_item(1, "u1") is True

    def test_missing_item_returns_false(self, db_mock):
        cur = MockCursor()
        cur.rowcount = 0
        db_mock.cursor = MagicMock(return_value=cur)
        assert db.delete_pantry_item(999, "u1") is False


# ── 정리 작업 ─────────────────────────────────────────────────────────────────

class TestCleanupOldTrash:
    def test_returns_deleted_row_count(self, db_mock):
        cur = MockCursor()
        cur.rowcount = 5
        db_mock.cursor = MagicMock(return_value=cur)
        assert db.cleanup_old_trash(30) == 5

    def test_commits(self, db_mock):
        db.cleanup_old_trash(30)
        db_mock.commit.assert_called_once()


class TestCleanupOldSessions:
    def test_returns_deleted_row_count(self, db_mock):
        cur = MockCursor()
        cur.rowcount = 3
        db_mock.cursor = MagicMock(return_value=cur)
        assert db.cleanup_old_sessions(7) == 3


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _dict_cursor(fetchone=None, fetchall=None):
    """RealDictCursor 스타일 MockCursor."""
    cur = MockCursor(is_dict=True)
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    return cur


def _plain_cursor(fetchone=None, fetchall=None, rowcount=0):
    """일반 cursor 스타일 MockCursor."""
    cur = MockCursor()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    cur.rowcount = rowcount
    return cur


def _raising_cursor(exc_class):
    """execute 호출 시 예외를 발생시키는 MockCursor."""
    cur = MockCursor()
    cur.execute.side_effect = exc_class()
    return cur
