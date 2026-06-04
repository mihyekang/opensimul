"""
PostgreSQL operations: image analyses, sessions, messages, grocery receipts.
"""

import hashlib
import json
import logging
import os
import secrets
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
                ALTER TABLE grocery_receipts
                ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_grocery_receipts_user ON grocery_receipts(user_id)")
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_code     TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pantry_items (
                    id          SERIAL PRIMARY KEY,
                    user_id     TEXT NOT NULL DEFAULT \'\',
                    raw_name    TEXT NOT NULL,
                    total_qty   INTEGER NOT NULL DEFAULT 0,
                    current_qty INTEGER NOT NULL DEFAULT 0,
                    unit        TEXT DEFAULT \'\',
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pantry_user_name ON pantry_items(user_id, raw_name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pantry_user ON pantry_items(user_id)")
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

def save_grocery_receipt(result: dict, user_id: str = "") -> int:
    """Pass 1 결과를 DB에 저장. receipt id 반환."""
    purchase_date = result.get("purchase_date") or None
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO grocery_receipts (purchase_date, merchant, total, currency, is_refund, raw_json, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                purchase_date,
                result.get("merchant"),
                result.get("total"),
                result.get("currency", "KRW"),
                result.get("is_refund", False),
                json.dumps(result, ensure_ascii=False),
                user_id,
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


def get_recent_groceries(days: int = 7, user_id: str = "") -> list[dict]:
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
                WHERE (r.purchase_date >= %s OR r.purchase_date IS NULL) AND NOT r.is_refund AND r.user_id = %s
                GROUP BY r.id
                ORDER BY r.purchase_date DESC, r.id DESC
            """, (cutoff, user_id))
            rows = cur.fetchall()
            return [{
                "id": r["id"],
                "merchant": r["merchant"],
                "purchase_date": r["purchase_date"].isoformat() if r["purchase_date"] else None,
                "total": r["total"],
                "items": r["items"] or [],
            } for r in rows]


def list_grocery_receipts(days: int = 30, user_id: str = "", start_date: str = "", end_date: str = "") -> list[dict]:
    """구매 이력 API용 요약 목록."""
    from_date = start_date if start_date else (date.today() - timedelta(days=days)).isoformat()
    to_date = end_date if end_date else date.today().isoformat()
    # start_date 명시 시 날짜 없는 영수증 제외 (기간 필터 엄격 적용)
    null_cond = "" if start_date else "OR r.purchase_date IS NULL"
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    r.id, r.merchant, r.purchase_date, r.total, r.is_refund, r.created_at,
                    COUNT(i.id) FILTER (WHERE NOT i.is_cancelled) AS item_count
                FROM grocery_receipts r
                LEFT JOIN grocery_items i ON i.receipt_id = r.id
                WHERE (r.purchase_date BETWEEN %s AND %s {null_cond}) AND r.user_id = %s
                GROUP BY r.id
                ORDER BY r.purchase_date DESC, r.created_at DESC
            """, (from_date, to_date, user_id))
            return [dict(r) for r in cur.fetchall()]


def get_grocery_receipt_detail(receipt_id: int, user_id: str) -> dict | None:
    """영수증 상세 조회 (품목 포함). 소유자만 조회 가능."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, merchant, purchase_date, total, is_refund, created_at, currency
                FROM grocery_receipts WHERE id = %s AND user_id = %s
            """, (receipt_id, user_id))
            row = cur.fetchone()
            if not row:
                return None
            result = dict(row)
            if result.get("purchase_date"):
                result["purchase_date"] = result["purchase_date"].isoformat()
            if result.get("created_at"):
                result["created_at"] = result["created_at"].isoformat()
            cur.execute("""
                SELECT id, raw_name, qty, unit_price, amount, is_cancelled
                FROM grocery_items WHERE receipt_id = %s ORDER BY id
            """, (receipt_id,))
            result["items"] = [dict(r) for r in cur.fetchall()]
        return result


def delete_grocery_receipt(receipt_id: int, user_id: str) -> bool:
    """영수증 삭제. user_id 소유자만 삭제 가능. True if deleted."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM grocery_receipts WHERE id = %s AND user_id = %s",
                (receipt_id, user_id),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def delete_grocery_item(item_id: int, user_id: str) -> bool:
    """품목 삭제. 해당 영수증 소유자만 가능."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM grocery_items
                WHERE id = %s AND receipt_id IN (
                    SELECT id FROM grocery_receipts WHERE user_id = %s
                )
            """, (item_id, user_id))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def add_grocery_item(receipt_id: int, raw_name: str, qty: int, unit_price: int | None, amount: int | None, user_id: str) -> dict | None:
    """품목 추가. 영수증 소유자만 가능. 추가된 품목 반환."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM grocery_receipts WHERE id = %s AND user_id = %s", (receipt_id, user_id))
            if not cur.fetchone():
                return None
            cur.execute("""
                INSERT INTO grocery_items (receipt_id, raw_name, qty, unit_price, amount, is_cancelled)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                RETURNING id, raw_name, qty, unit_price, amount, is_cancelled
            """, (receipt_id, raw_name, qty, unit_price, amount))
            row = dict(cur.fetchone())
        conn.commit()
    return row


def update_grocery_item(item_id: int, raw_name: str | None, qty: int | None, unit_price: int | None, amount: int | None, user_id: str) -> bool:
    """품목 수정. 영수증 소유자만 가능."""
    updates, params = [], []
    if raw_name  is not None: updates.append("raw_name = %s");   params.append(raw_name)
    if qty       is not None: updates.append("qty = %s");        params.append(qty)
    if unit_price is not None: updates.append("unit_price = %s"); params.append(unit_price)
    if amount    is not None: updates.append("amount = %s");     params.append(amount)
    if not updates:
        return False
    params.extend([item_id, user_id])
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE grocery_items SET {', '.join(updates)}
                WHERE id = %s AND receipt_id IN (
                    SELECT id FROM grocery_receipts WHERE user_id = %s
                )
            """, params)
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def update_grocery_receipt_meta(receipt_id: int, merchant: str | None, purchase_date: str | None, user_id: str, total: int | None = None) -> bool:
    """영수증 메타데이터 수정. 소유자만 가능. True if updated."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            updates = []
            params = []
            if merchant is not None:
                updates.append("merchant = %s")
                params.append(merchant)
            if purchase_date is not None:
                updates.append("purchase_date = %s")
                params.append(purchase_date)
            if total is not None:
                updates.append("total = %s")
                params.append(total)
            if not updates:
                return False
            params.extend([receipt_id, user_id])
            sql = f"UPDATE grocery_receipts SET {', '.join(updates)} WHERE id = %s AND user_id = %s"
            cur.execute(sql, params)
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def search_receipts_by_merchant(merchant: str, days: int, user_id: str) -> list[dict]:
    """업체명 부분 일치로 영수증 검색."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT r.id, r.merchant, r.purchase_date, r.total, r.is_refund,
                       COUNT(i.id) FILTER (WHERE NOT i.is_cancelled) AS item_count
                FROM grocery_receipts r
                LEFT JOIN grocery_items i ON i.receipt_id = r.id
                WHERE r.merchant ILIKE %s AND r.user_id = %s
                  AND (r.purchase_date >= %s OR r.purchase_date IS NULL)
                GROUP BY r.id
                ORDER BY r.purchase_date DESC, r.id DESC
            """, (f"%{merchant}%", user_id, cutoff))
            return [{
                **dict(r),
                "purchase_date": r["purchase_date"].isoformat() if r["purchase_date"] else None,
            } for r in cur.fetchall()]


def search_grocery_items(keyword: str, days: int, user_id: str) -> list[dict]:
    """품목명 키워드로 구매 이력 검색."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.id, i.raw_name, i.qty, i.unit_price, i.amount,
                       r.merchant, r.purchase_date, r.id AS receipt_id
                FROM grocery_items i
                JOIN grocery_receipts r ON r.id = i.receipt_id
                WHERE i.raw_name ILIKE %s AND r.user_id = %s
                  AND NOT i.is_cancelled
                  AND (r.purchase_date >= %s OR r.purchase_date IS NULL)
                ORDER BY r.purchase_date DESC, i.id DESC
                LIMIT 50
            """, (f"%{keyword}%", user_id, cutoff))
            return [{
                **dict(r),
                "purchase_date": r["purchase_date"].isoformat() if r["purchase_date"] else None,
            } for r in cur.fetchall()]


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




# ── pantry ─────────────────────────────────────────────────────────────────────

def upsert_pantry_from_purchase(user_id: str, raw_name: str, qty: int, unit: str) -> None:
    """영수증 저장 시 pantry 재고에 추가 (없으면 신규 생성)."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pantry_items (user_id, raw_name, total_qty, current_qty, unit)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, raw_name)
                DO UPDATE SET
                    total_qty   = %s,
                    current_qty = pantry_items.current_qty + %s,
                    unit        = CASE WHEN %s != \'\' THEN %s ELSE pantry_items.unit END,
                    updated_at  = NOW()
            """, (user_id, raw_name, qty, qty, unit, qty, qty, unit, unit))
        conn.commit()


def list_pantry(user_id: str) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, raw_name, total_qty, current_qty, unit, updated_at
                FROM pantry_items WHERE user_id = %s
                ORDER BY updated_at DESC
            """, (user_id,))
            return [{
                **dict(r),
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            } for r in cur.fetchall()]


def add_pantry_item(user_id: str, raw_name: str, total_qty: int, current_qty: int, unit: str) -> dict:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO pantry_items (user_id, raw_name, total_qty, current_qty, unit)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, raw_name)
                DO UPDATE SET total_qty = %s, current_qty = %s, unit = %s, updated_at = NOW()
                RETURNING id, raw_name, total_qty, current_qty, unit, updated_at
            """, (user_id, raw_name, total_qty, current_qty, unit, total_qty, current_qty, unit))
            row = dict(cur.fetchone())
        conn.commit()
    if row.get("updated_at"):
        row["updated_at"] = row["updated_at"].isoformat()
    return row


def update_pantry_item(item_id: int, user_id: str, raw_name: str | None, total_qty: int | None, current_qty: int | None, unit: str | None) -> bool:
    updates, params = [], []
    if raw_name   is not None: updates.append("raw_name = %s");    params.append(raw_name)
    if total_qty  is not None: updates.append("total_qty = %s");   params.append(total_qty)
    if current_qty is not None: updates.append("current_qty = %s"); params.append(current_qty)
    if unit       is not None: updates.append("unit = %s");        params.append(unit)
    if not updates:
        return False
    updates.append("updated_at = NOW()")
    params.extend([item_id, user_id])
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE pantry_items SET {', '.join(updates)} WHERE id = %s AND user_id = %s", params)
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def delete_pantry_item(item_id: int, user_id: str) -> bool:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pantry_items WHERE id = %s AND user_id = %s", (item_id, user_id))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def deduct_pantry_qty(user_id: str, keyword: str, qty_used: int) -> dict:
    """재료 사용 시 현재 수량 차감. GREATEST(0, ...) 으로 음수 방지."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE pantry_items
                SET current_qty = GREATEST(0, current_qty - %s), updated_at = NOW()
                WHERE user_id = %s AND raw_name ILIKE %s
                RETURNING id, raw_name, total_qty, current_qty, unit
            """, (qty_used, user_id, f"%{keyword}%"))
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
    if not rows:
        return {"updated": False, "keyword": keyword, "message": "해당 품목을 찾을 수 없습니다."}
    return {
        "updated": True,
        "items": [{"name": r["raw_name"], "total_qty": r["total_qty"], "current_qty": r["current_qty"], "unit": r["unit"] or "개"} for r in rows],
    }

# ── user auth ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return secrets.compare_digest(check.hex(), h)
    except Exception:
        return False


def login_user(user_code: str, password: str) -> bool:
    """코드+비밀번호 검증. 일치하면 True."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT password_hash FROM users WHERE user_code = %s", (user_code,))
            row = cur.fetchone()
            if not row:
                return False
            return _verify_password(password, row["password_hash"])


def register_user(user_code: str, password: str) -> bool:
    """신규 코드 생성. 이미 존재하면 False."""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_code, password_hash) VALUES (%s, %s)",
                    (user_code, _hash_password(password)),
                )
            conn.commit()
        return True
    except Exception:
        return False
