"""
PostgreSQL operations: image analyses, sessions, messages.
"""

import logging
import os

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


def _get_conn():
    return psycopg2.connect(os.environ["POSTGRESQL_CONNECTION_STRING"])


def init_db() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS image_analyses (
                    id                SERIAL PRIMARY KEY,
                    created_at        TIMESTAMPTZ DEFAULT NOW(),
                    filename          TEXT,
                    prompt            TEXT,
                    analysis          TEXT,
                    prompt_tokens     INTEGER,
                    completion_tokens INTEGER,
                    cost_usd          NUMERIC(14, 8)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    last_active TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id         SERIAL PRIMARY KEY,
                    session_id TEXT REFERENCES sessions(session_id) ON DELETE CASCADE,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at)")
        conn.commit()


# ── session ────────────────────────────────────────────────────────────────────

def touch_session(session_id: str) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sessions (session_id)
                VALUES (%s)
                ON CONFLICT (session_id) DO UPDATE SET last_active = NOW()
            """, (session_id,))
        conn.commit()


def load_messages(session_id: str, limit: int = 40) -> list[dict]:
    """Return last `limit` messages for a session in chronological order."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM messages WHERE session_id = %s
                    ORDER BY created_at DESC LIMIT %s
                ) sub ORDER BY created_at ASC
            """, (session_id, limit))
            return [dict(r) for r in cur.fetchall()]


def save_messages(session_id: str, messages: list[dict]) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            for msg in messages:
                cur.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (%s, %s, %s)",
                    (session_id, msg["role"], msg["content"]),
                )
        conn.commit()


def clear_messages(session_id: str) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
        conn.commit()


def cleanup_old_sessions(days: int = 7) -> int:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE last_active < NOW() - INTERVAL '%s days'",
                (days,),
            )
            count = cur.rowcount
        conn.commit()
    logger.info("만료 세션 %d개 삭제 (기준: %d일)", count, days)
    return count


# ── image analyses ─────────────────────────────────────────────────────────────

def save_analysis(filename, prompt, analysis, prompt_tokens, completion_tokens, cost_usd) -> dict:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO image_analyses
                    (filename, prompt, analysis, prompt_tokens, completion_tokens, cost_usd)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (filename, prompt, analysis, prompt_tokens, completion_tokens, cost_usd),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def list_analyses(limit: int = 50) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, created_at, filename, prompt,
                       LEFT(analysis, 200) AS analysis_preview,
                       prompt_tokens, completion_tokens, cost_usd
                FROM image_analyses ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
