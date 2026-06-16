"""
pytest 공통 픽스처 — MockCursor/MockConnection으로 DB 격리.

psycopg2 사용 패턴:
    with _get_conn() as conn:
        with conn.cursor() as cur:                          # 일반 cursor
        with conn.cursor(cursor_factory=RealDictCursor) as cur:  # dict cursor
        cur.execute(sql, params)
        row = cur.fetchone()
        rows = cur.fetchall()
    conn.commit()
"""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


# ── Mock DB ───────────────────────────────────────────────────────────────────

class MockCursor:
    """psycopg2 cursor 흉내.

    테스트에서 반환값 설정:
        cur.fetchone.return_value = {"id": 1}    # dict (RealDictCursor 스타일)
        cur.fetchone.return_value = (1,)          # tuple
        cur.fetchall.return_value = [{"id": 1}]
        cur.rowcount = 3
    """
    def __init__(self, is_dict: bool = False):
        self.is_dict = is_dict
        self.execute = MagicMock()
        self.fetchone = MagicMock(return_value=None)
        self.fetchall = MagicMock(return_value=[])
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MockConnection:
    """psycopg2 connection 흉내.

    cursor() 호출마다 새 MockCursor 반환.
    cursor_factory=RealDictCursor 여부를 is_dict 플래그로 추적.
    """
    def __init__(self):
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self._cursors: list[MockCursor] = []

    def cursor(self, cursor_factory=None):
        from psycopg2.extras import RealDictCursor
        cur = MockCursor(is_dict=(cursor_factory is RealDictCursor))
        self._cursors.append(cur)
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def last_cursor(self) -> MockCursor:
        return self._cursors[-1]

    @property
    def all_cursors(self) -> list[MockCursor]:
        return self._cursors


def cursor_sequence(conn: MockConnection, *specs: dict):
    """conn.cursor()가 specs 순서대로 다른 MockCursor를 반환하도록 설정.

    사용 예:
        cursor_sequence(mock_conn,
            {"fetchone": None},            # 1번째 cursor: 중복 없음
            {"fetchone": (42,)},           # 2번째 cursor: INSERT RETURNING id
        )
    """
    cursors = []
    for spec in specs:
        cur = MockCursor(is_dict=spec.get("is_dict", False))
        cur.fetchone.return_value = spec.get("fetchone")
        cur.fetchall.return_value = spec.get("fetchall", [])
        cur.rowcount = spec.get("rowcount", 0)
        cursors.append(cur)
    conn.cursor = MagicMock(side_effect=cursors)
    return cursors


# ── 픽스처 ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_conn():
    """빈 MockConnection 반환."""
    return MockConnection()


@pytest.fixture
def db_mock(mock_conn):
    """db._connection_factory를 MockConnection으로 교체. 테스트 후 자동 복원."""
    import db
    db.set_connection_factory(lambda: mock_conn)
    yield mock_conn
    db.set_connection_factory(None)


@pytest.fixture(autouse=True)
def reset_app_state():
    """각 테스트 전후 전역 state와 rate limiter 초기화."""
    import app as _app
    _app._reset_for_testing(db_enabled=False)
    yield
    _app._reset_for_testing(db_enabled=False)


def _make_test_client(db_enabled: bool = True):
    """lifespan 의존성(ClientConfig, fetch_usd_to_krw)을 패치한 TestClient 생성."""
    from unittest.mock import patch, MagicMock
    import app as _app
    from app import app

    _app._reset_for_testing(db_enabled=db_enabled)
    mock_config = MagicMock()
    mock_config.verify_ssl = False

    ctx = patch.multiple(
        "app",
        ClientConfig=MagicMock(return_value=mock_config),
        fetch_usd_to_krw=MagicMock(return_value=(1300.0, "test")),
    )
    ctx.start()
    client = TestClient(app, raise_server_exceptions=True)
    return client, ctx


@pytest.fixture
def client(db_mock):
    """DB 활성화 상태의 FastAPI TestClient (비로그인).

    lifespan이 POSTGRESQL_CONNECTION_STRING 없으면 db_enabled=False로 재설정하므로
    TestClient가 시작된 후 명시적으로 True로 설정.
    """
    import app as _app
    from app import app
    from unittest.mock import patch, MagicMock

    mock_config = MagicMock()
    mock_config.verify_ssl = False

    with patch("app.ClientConfig", return_value=mock_config), \
         patch("app.fetch_usd_to_krw", return_value=(1300.0, "test")):
        with TestClient(app, raise_server_exceptions=True) as c:
            _app.state.db_enabled = True   # lifespan 이후 강제 설정
            _app._rate_limiter.reset_all()
            yield c


@pytest.fixture
def authed_client(db_mock):
    """user_code='testuser'로 고정된 인증 TestClient."""
    import app as _app
    from app import app, get_current_user
    from unittest.mock import patch, MagicMock

    mock_config = MagicMock()
    mock_config.verify_ssl = False
    app.dependency_overrides[get_current_user] = lambda: "testuser"

    with patch("app.ClientConfig", return_value=mock_config), \
         patch("app.fetch_usd_to_krw", return_value=(1300.0, "test")):
        with TestClient(app, raise_server_exceptions=True) as c:
            _app.state.db_enabled = True   # lifespan 이후 강제 설정
            _app._rate_limiter.reset_all()
            yield c

    app.dependency_overrides.clear()
