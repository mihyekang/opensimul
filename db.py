"""
PostgreSQL operations for storing image analysis results.
Requires: POSTGRESQL_CONNECTION_STRING env var
  e.g. postgresql://user:pass@host:5432/dbname?sslmode=require
"""

import os

import psycopg2
from psycopg2.extras import RealDictCursor


def _get_conn():
    return psycopg2.connect(os.environ["POSTGRESQL_CONNECTION_STRING"])


def init_db() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS image_analyses (
                    id            SERIAL PRIMARY KEY,
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    filename      TEXT,
                    prompt        TEXT,
                    analysis      TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    cost_usd      NUMERIC(14, 8)
                )
            """)
        conn.commit()


def save_analysis(
    filename: str,
    prompt: str,
    analysis: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> dict:
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
            cur.execute(
                """
                SELECT id, created_at, filename, prompt,
                       LEFT(analysis, 200) AS analysis_preview,
                       prompt_tokens, completion_tokens, cost_usd
                FROM image_analyses
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
