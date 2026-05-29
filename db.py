"""
PostgreSQL operations: image analyses, sessions, messages, grocery receipts.
"""

import json
import logging
import os
from datetime import date, timedelta

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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS grocery_receipts (
                    id            SERIAL PRIMARY KEY,
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    purchase_date DATE,
                    merchant      TEXT,
                    total         INTEGER,
                    currency      TEXT DEFAULT 'KRW',
                    is_refund     BOOLEAN DEFAULT FALSE,
                    raw_json      JSONB
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_grocery_receipts_date ON grocery_receipts(purchase_date DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS grocery_items (
                    id           SERIAL PRIMARY KEY,
                    receipt_id   INTEGER REFERENCES grocery_receipts(id) ON DELETE CASCADE,
                    raw_name     TEXT NOT NULL,
                    qty          INTEGER DEFAULT 1,
                    unit_price   INTEGER,
                    amount       INTEGER,
                    is_cancelled BOOLEAN DEFAULT FALSE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_grocery_items_receipt ON grocery_items(receipt_id)")
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
    cutoff = date.today() - timedelta(days=days)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE last_active < %s",
                (cutoff,),
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


# ── grocery receipts ───────────────────────────────────────────────────────────

def save_grocery_receipt(result: dict) -> int:
    """Pass 1 결과를 DB에 저장. receipt id 반환."""
    purchase_date = result.get("purchase_date") or None
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO grocery_receipts (purchase_date, merchant, total, currency, is_refund, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                purchase_date,
                result.get("merchant"),
                result.get("total"),
                result.get("currency", "KRW"),
                result.get("is_refund", False),
                json.dumps(result, ensure_ascii=False),
            ))
            receipt_id = cur.fetchone()[0]
            for item in result.get("items", []):
                cur.execute("""
                    INSERT INTO grocery_items (receipt_id, raw_name, qty, unit_price, amount, is_cancelled)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    receipt_id,
                    item.get("raw_name", ""),
                    item.get("qty", 1),
                    item.get("unit_price"),
                    item.get("amount"),
                    item.get("is_cancelled", False),
                ))
        conn.commit()
    return receipt_id


def get_recent_groceries(days: int = 7) -> list[dict]:
    """최근 N일 구매 이력을 영수증+품목 포함해서 반환 (챗봇 컨텍스트용)."""
    cutoff = date.today() - timedelta(days=days)
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    r.id, r.merchant, r.purchase_date, r.total, r.is_refund,
                    COALESCE(
                        json_agg(
                            json_build_object('raw_name', i.raw_name, 'amount', i.amount)
                            ORDER BY i.id
                        ) FILTER (WHERE i.id IS NOT NULL AND NOT i.is_cancelled AND i.amount > 0),
                        '[]'::json
                    ) AS items
                FROM grocery_receipts r
                LEFT JOIN grocery_items i ON i.receipt_id = r.id
                WHERE r.purchase_date >= %s AND NOT r.is_refund
                GROUP BY r.id
                ORDER BY r.purchase_date DESC, r.id DESC
            """, (cutoff,))
            rows = cur.fetchall()
            return [{
                "id": r["id"],
                "merchant": r["merchant"],
                "purchase_date": r["purchase_date"].isoformat() if r["purchase_date"] else None,
                "total": r["total"],
                "items": r["items"] or [],
            } for r in rows]


def list_grocery_receipts(days: int = 30) -> list[dict]:
    """구매 이력 API용 요약 목록."""
    cutoff = date.today() - timedelta(days=days)
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    r.id, r.merchant, r.purchase_date, r.total, r.is_refund, r.created_at,
                    COUNT(i.id) FILTER (WHERE NOT i.is_cancelled) AS item_count
                FROM grocery_receipts r
                LEFT JOIN grocery_items i ON i.receipt_id = r.id
                WHERE r.purchase_date >= %s
                GROUP BY r.id
                ORDER BY r.purchase_date DESC, r.created_at DESC
            """, (cutoff,))
            return [dict(r) for r in cur.fetchall()]


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
