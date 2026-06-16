"""
FastAPI web server for Azure OpenAI chat.
Run: uvicorn app:app --reload
"""

import asyncio
import base64
import difflib
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import fitz  # PyMuPDF
from PIL import Image, ImageOps

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from pydantic import BaseModel, Field, field_validator

import db
from agent_tools import TOOLS, execute_tool
from azure_openai_client import ClientConfig
from grocery_pass1 import extract_pass1_bytes, extract_pass1_text, validate
from repl import fetch_usd_to_krw
from session_manager import SessionManager

logger = logging.getLogger(__name__)
SESSION_RETENTION_DAYS = 7
MAX_IMAGE_W = 1400  # 가로 상한 (landscape / wide 사진용)
MAX_IMAGE_H = 9000  # 세로 상한 (매우 긴 스크린샷 극단치만 제한)


def _resize_image(content: bytes, mime_type: str) -> tuple[bytes, str]:
    """가로가 넓거나(>1400) 세로가 극단적으로 길(>9000)때만 리사이즈. EXIF 회전 보정."""
    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        w, h = img.size
        scale = 1.0
        if w > MAX_IMAGE_W:
            scale = min(scale, MAX_IMAGE_W / w)
        if h > MAX_IMAGE_H:
            scale = min(scale, MAX_IMAGE_H / h)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        logger.warning("이미지 리사이즈 실패 — 원본 사용", exc_info=True)
        return content, mime_type


class AppState:
    session_manager: SessionManager
    usd_to_krw: float
    rate_date: str
    db_enabled: bool


state = AppState()


_KST = timezone(timedelta(hours=9))
_TRASH_CLEANUP_DAYS = 30


async def _daily_trash_cleanup() -> None:
    """매일 오전 8시(KST)에 30일 이상 지난 휴지통 항목을 자동 삭제."""
    while True:
        now = datetime.now(_KST)
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            loop = asyncio.get_event_loop()
            deleted = await loop.run_in_executor(
                None, lambda: db.cleanup_old_trash(_TRASH_CLEANUP_DAYS)
            )
            logger.info("휴지통 자동 정리: %d개 삭제", deleted)
        except Exception:
            logger.exception("휴지통 자동 정리 중 오류 발생")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ClientConfig()
    state.usd_to_krw, state.rate_date = fetch_usd_to_krw(verify_ssl=config.verify_ssl)
    state.db_enabled = False

    cleanup_task = None
    if os.environ.get("POSTGRESQL_CONNECTION_STRING"):
        try:
            db.init_db()
            state.db_enabled = True
            loop = asyncio.get_event_loop()
            deleted = await loop.run_in_executor(
                None, lambda: db.cleanup_old_sessions(SESSION_RETENTION_DAYS)
            )
            logger.info("만료 채팅 세션 %d개 정리 완료", deleted)
            expired = await loop.run_in_executor(None, db.cleanup_expired_user_sessions)
            logger.info("만료 로그인 세션 %d개 정리 완료", expired)
            cleanup_task = asyncio.create_task(_daily_trash_cleanup())
        except Exception as e:
            logger.warning("DB 초기화 실패 — DB 저장 비활성화: %s", e)

    state.session_manager = SessionManager(config, db_enabled=state.db_enabled)
    yield

    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """응답마다 보안 HTTP 헤더를 추가한다."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "frame-ancestors 'none';"
        )
        return response


app = FastAPI(title="Azure OpenAI Chat", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------- models ----------

class AuthRequest(BaseModel):
    user_code: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    user_code: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password_complexity(cls, v: str) -> str:
        if not any(c.islower() for c in v) or not any(c.isupper() for c in v):
            raise ValueError("비밀번호는 대문자와 소문자를 포함해야 합니다")
        return v


class SpellCheckRequest(BaseModel):
    names: list[str]


class PushTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    platform: str = Field(default="ios", max_length=16)


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


class ResetRequest(BaseModel):
    session_id: str = ""
    system_prompt: str = "You are a helpful assistant."


class MemorySetRequest(BaseModel):
    key: str
    value: str


class GrocerySaveRequest(BaseModel):
    result: dict


class GroceryUpdateRequest(BaseModel):
    merchant: str | None = None
    purchase_date: str | None = None
    total: int | None = None


class GroceryItemAddRequest(BaseModel):
    raw_name: str = Field(min_length=1, max_length=200)
    qty: int = 1
    unit_price: int | None = None
    amount: int | None = None


class GroceryItemUpdateRequest(BaseModel):
    raw_name: str | None = Field(default=None, max_length=200)
    qty: int | None = None
    unit_price: int | None = None
    amount: int | None = None


class TextExtractRequest(BaseModel):
    text: str = Field(max_length=20_000)


class ChatNoteRequest(BaseModel):
    session_id: str
    user_note: str
    assistant_note: str


class PantryAddRequest(BaseModel):
    raw_name: str = Field(min_length=1, max_length=200)
    total_qty: int = 0
    current_qty: int = 0
    unit: str = Field(default="개", max_length=20)


class PantryUpdateRequest(BaseModel):
    raw_name: str | None = Field(default=None, max_length=200)
    total_qty: int | None = None
    current_qty: int | None = None
    unit: str | None = Field(default=None, max_length=20)


class TrashMoveRequest(BaseModel):
    start_date: str
    end_date: str
    merchant: str = ""


# ---------- helpers ----------

def _client(session_id: str):
    return state.session_manager.get_client(session_id)


async def get_current_user(sid: str = Cookie(default="", alias="sid")) -> str:
    """sid 쿠키(랜덤 토큰)를 user_code로 변환하는 FastAPI 의존성.
    DB가 비활성화된 경우 sid 값을 그대로 user_code로 사용(fallback).
    """
    if not sid:
        return ""
    if not state.db_enabled:
        return sid
    loop = asyncio.get_event_loop()
    try:
        user_id = await loop.run_in_executor(None, lambda: db.get_user_from_session(sid))
        return user_id or ""
    except Exception:
        logger.exception("세션 조회 실패")
        return ""


def _validate_date(s: str, field: str) -> None:
    """ISO 날짜 형식(YYYY-MM-DD) 검증. 잘못된 형식이면 400 반환."""
    if not s:
        return
    try:
        date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid_date: {field}")


def _build_purchase_summary(rows: list[dict]) -> str:
    """최근 구매 이력을 챗봇 시스템 프롬프트용 컴팩트 텍스트로 변환."""
    if not rows:
        return ""
    # 중복 제거: 같은 업체·날짜·금액 조합은 1건만 포함
    seen: set = set()
    deduped = []
    for r in rows:
        key = (r.get("merchant"), r.get("purchase_date"), r.get("total"))
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    lines = ["[최근 구매 내역]"]
    for r in deduped[:10]:
        merchant = r.get("merchant") or "알 수 없음"
        pdate = r.get("purchase_date") or ""
        total = r.get("total")
        items = r.get("items") or []
        item_names = [i["raw_name"] for i in items if i.get("raw_name") and (i.get("amount") or 0) > 0]
        item_str = ", ".join(item_names[:4])
        if len(item_names) > 4:
            item_str += f" 외 {len(item_names)-4}건"
        total_str = f" 합계 {total:,}원" if total else ""
        lines.append(f"{pdate} {merchant}: {item_str}{total_str}")
    return "\n".join(lines)


def _build_usage(client, usage, turn_cost_usd: float) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "turn_cost_usd": turn_cost_usd,
        "turn_cost_krw": turn_cost_usd * state.usd_to_krw,
        "total_tokens": client.total_tokens,
        "total_cost_usd": client.total_cost,
        "total_cost_krw": client.total_cost * state.usd_to_krw,
    }


async def _save_turn(session_id: str, user_msg: str, assistant_msg: str):
    if state.db_enabled and session_id:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: (
            db.touch_session(session_id),
            db.save_messages(session_id, [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ])
        ))


# ---------- routes ----------

@app.get("/")
async def index():
    return RedirectResponse(url="/chat")


@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/info")
async def info(session_id: str = ""):
    client = _client(session_id)
    return {
        "deployment": client.config.deployment,
        "usd_to_krw": state.usd_to_krw,
        "rate_date": state.rate_date,
        "total_tokens": client.total_tokens,
        "total_cost_usd": client.total_cost,
        "total_cost_krw": client.total_cost * state.usd_to_krw,
        "db_enabled": state.db_enabled,
        "current_turns": client.current_turns,
        "max_history_turns": client.config.max_history_turns,
    }


@app.post("/reset")
async def reset(req: ResetRequest):
    state.session_manager.invalidate(req.session_id)
    if state.db_enabled and req.session_id:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: db.clear_messages(req.session_id))
    return {"ok": True}


@app.get("/memory")
async def get_memory(session_id: str = ""):
    return _client(session_id).get_memory()


@app.post("/memory")
async def set_memory(req: MemorySetRequest, session_id: str = ""):
    c = _client(session_id)
    c.set_memory(req.key, req.value)
    return {"ok": True, "memory": c.get_memory()}


@app.delete("/memory/{key}")
async def delete_memory(key: str, session_id: str = ""):
    c = _client(session_id)
    existed = c.delete_memory(key)
    return {"ok": existed, "memory": c.get_memory()}


@app.delete("/memory")
async def clear_memory(session_id: str = ""):
    _client(session_id).clear_memory()
    return {"ok": True}


# ---------- chat ----------

async def _inject_grocery_context(c, user_id: str = "") -> None:
    """최근 7일 구매 이력을 클라이언트 transient context에 주입."""
    if not state.db_enabled:
        return
    loop = asyncio.get_event_loop()
    try:
        rows = await loop.run_in_executor(None, lambda: db.get_recent_groceries(7, user_id))
        summary = _build_purchase_summary(rows)
        if summary:
            c.set_transient("grocery", summary)
        else:
            c.clear_transient("grocery")
    except Exception:
        logger.exception("구매 이력 조회 실패")


@app.post("/chat")
async def chat(req: ChatRequest, user_id: str = Depends(get_current_user)):
    c = _client(req.session_id)
    await _inject_grocery_context(c, user_id)
    loop = asyncio.get_event_loop()
    if state.db_enabled and user_id:
        tool_executor = lambda name, args: execute_tool(name, args, user_id)
        reply, usage = await loop.run_in_executor(
            None, lambda: c.chat_with_tools(req.message, TOOLS, tool_executor)
        )
    else:
        reply, usage = await loop.run_in_executor(None, lambda: c.chat(req.message))
    turn_cost_usd = usage.cost(c.config.input_price_per_m, c.config.output_price_per_m)
    await _save_turn(req.session_id, req.message, reply)
    return {"reply": reply, "usage": _build_usage(c, usage, turn_cost_usd)}


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(get_current_user)):
    c = _client(req.session_id)
    await _inject_grocery_context(c, user_id)

    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        collected: list[str] = []

        def produce():
            try:
                if state.db_enabled and user_id:
                    tool_executor = lambda name, args: execute_tool(name, args, user_id)
                    for item in c.stream_chat_with_tools(req.message, TOOLS, tool_executor):
                        loop.call_soon_threadsafe(queue.put_nowait, item)
                else:
                    for token in c.stream_chat(req.message):
                        loop.call_soon_threadsafe(queue.put_nowait, {"token": token})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, produce)

        while True:
            item = await queue.get()
            if item is None:
                break
            if "token" in item:
                collected.append(item["token"])
                yield f"data: {json.dumps({'token': item['token']})}\n\n"
            elif "tool_call" in item:
                yield f"data: {json.dumps({'tool_call': item['tool_call'], 'args': item.get('args', {})})}\n\n"

        assistant_reply = "".join(collected)
        await _save_turn(req.session_id, req.message, assistant_reply)

        last = c.last_usage
        if last:
            turn_cost_usd = last.cost(c.config.input_price_per_m, c.config.output_price_per_m)
            yield f"data: {json.dumps({'done': True, 'usage': _build_usage(c, last, turn_cost_usd)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/chat/history")
async def chat_history(session_id: str = "", limit: int = Query(default=40, ge=1, le=200)):
    """이전 대화 이력 반환 (UI 복원용)."""
    if not state.db_enabled or not session_id:
        return []
    loop = asyncio.get_event_loop()
    msgs = await loop.run_in_executor(
        None, lambda: db.load_messages(session_id, limit=limit)
    )
    return msgs


@app.post("/chat/note")
async def chat_note(req: ChatNoteRequest):
    """LLM 호출 없이 대화 이력에 노트 주입 (영수증 삭제 알림 등)."""
    c = _client(req.session_id)
    c.inject_turn(req.user_note, req.assistant_note)
    await _save_turn(req.session_id, req.user_note, req.assistant_note)
    return {"ok": True}


# ---------- image analysis ----------

@app.post("/analyze")
async def analyze(
    image: UploadFile = File(...),
    prompt: str = Form(default="이 이미지를 자세히 분석해주세요."),
    session_id: str = Form(default=""),
):
    c = _client(session_id)
    content = await image.read()
    mime_type = image.content_type or "image/jpeg"
    image_b64 = base64.b64encode(content).decode()

    loop = asyncio.get_event_loop()
    analysis, usage = await loop.run_in_executor(
        None, lambda: c.analyze_image(image_b64, mime_type, prompt)
    )
    turn_cost_usd = usage.cost(c.config.input_price_per_m, c.config.output_price_per_m)

    saved_id = None
    if state.db_enabled:
        row = await loop.run_in_executor(
            None,
            lambda: db.save_analysis(
                image.filename or "unknown", prompt, analysis,
                usage.prompt_tokens, usage.completion_tokens, turn_cost_usd,
            ),
        )
        saved_id = row["id"]

    return {
        "analysis": analysis,
        "saved_id": saved_id,
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "turn_cost_usd": turn_cost_usd,
            "turn_cost_krw": turn_cost_usd * state.usd_to_krw,
        },
    }


@app.get("/grocery", response_class=HTMLResponse)
async def grocery_page():
    with open("static/grocery.html", encoding="utf-8") as f:
        return f.read()


@app.post("/grocery/extract")
async def grocery_extract(image: UploadFile = File(...)):
    """Pass 1: 영수증 이미지/PDF → JSON 추출 + 검증."""
    content = await image.read()
    mime_type = image.content_type or "image/jpeg"

    # PDF → JPEG 변환 (첫 페이지, 150 DPI)
    if mime_type == "application/pdf" or (image.filename or "").lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")
        if len(doc) == 0:
            raise HTTPException(status_code=400, detail="pdf_empty")
        pix = doc[0].get_pixmap(dpi=150)
        content = pix.tobytes("jpeg")
        mime_type = "image/jpeg"

    # 이미지 리사이즈 (최장 변 1568px 이하로, EXIF 회전 보정)
    content, mime_type = _resize_image(content, mime_type)

    config = ClientConfig()
    loop = asyncio.get_event_loop()
    result, raw = await loop.run_in_executor(
        None, lambda: extract_pass1_bytes(content, mime_type, config)
    )
    if not result.get("purchase_date"):
        result["purchase_date"] = date.today().isoformat()
        result["_date_inferred"] = True
    issues = validate(result)
    return {"result": result, "issues": issues, "raw": raw}


@app.post("/grocery/extract-text")
async def grocery_extract_text(req: TextExtractRequest):
    """Pass 1: 붙여넣은 영수증 텍스트 → JSON 추출 + 검증."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    config = ClientConfig()
    loop = asyncio.get_event_loop()
    result, raw = await loop.run_in_executor(
        None, lambda: extract_pass1_text(req.text.strip(), config)
    )
    if not result.get("purchase_date"):
        result["purchase_date"] = date.today().isoformat()
        result["_date_inferred"] = True
    issues = validate(result)
    return {"result": result, "issues": issues, "raw": raw}


@app.post("/grocery/save")
async def grocery_save(req: GrocerySaveRequest, user_id: str = Depends(get_current_user)):
    """Pass 1 추출 결과를 DB에 저장."""
    if not state.db_enabled:
        return {"ok": False, "reason": "db_not_enabled"}
    from pantry_utils import infer_qty
    loop = asyncio.get_event_loop()
    receipt_id = await loop.run_in_executor(
        None, lambda: db.save_grocery_receipt(req.result, user_id)
    )
    if user_id:
        for item in req.result.get("items", []):
            if item.get("is_cancelled"):
                continue
            if not (item.get("amount") or 0) > 0:
                continue
            raw = (item.get("raw_name") or "").strip()
            if not raw:
                continue
            qty, unit = infer_qty(raw, item.get("qty") or 1)
            await loop.run_in_executor(
                None, lambda r=raw, q=qty, u=unit: db.upsert_pantry_from_purchase(user_id, r, q, u)
            )
    return {"ok": True, "receipt_id": receipt_id}


@app.get("/grocery/history")
async def grocery_history(
    days: int = Query(default=30, ge=1, le=365),
    start_date: str = "",
    end_date: str = "",
    user_id: str = Depends(get_current_user),
):
    """최근 N일 구매 이력 반환."""
    _validate_date(start_date, "start_date")
    _validate_date(end_date, "end_date")
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: db.list_grocery_receipts(days, user_id, start_date, end_date))
    for r in rows:
        if r.get("purchase_date"):
            r["purchase_date"] = r["purchase_date"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return rows


@app.get("/grocery/recent")
async def grocery_recent(days: int = Query(default=90, ge=1, le=365), user_id: str = Depends(get_current_user)):
    """카드 렌더링용: 최근 N일 구매 이력 (품목 포함)."""
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: db.get_recent_groceries(days, user_id))
    return rows


class PurchaseAnalysisRequest(BaseModel):
    start_date: str
    end_date: str


@app.post("/grocery/analyze-purchases")
async def analyze_purchases(req: PurchaseAnalysisRequest, user_id: str = Depends(get_current_user)):
    """기간 내 구매 아이템 AI 카테고리 분류 + 패턴 분석."""
    _validate_date(req.start_date, "start_date")
    _validate_date(req.end_date, "end_date")
    if not state.db_enabled:
        return {"summary": "", "categories": [], "items": []}

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(
        None, lambda: db.get_items_by_date_range(user_id, req.start_date, req.end_date)
    )
    if not items:
        return {"summary": "", "categories": [], "items": []}

    item_list = [{"품목": i["raw_name"], "금액": i["amount"]} for i in items]
    prompt = (
        "다음 구매 품목 목록과 금액을 분석해줘.\n\n"
        f"품목 및 금액: {json.dumps(item_list, ensure_ascii=False)}\n\n"
        "응답을 반드시 아래 JSON 형식으로만 출력해 (다른 텍스트 없이):\n"
        "{\n"
        '  "summary": "금액을 포함한 소비 경향 3줄 이내. 줄바꿈은 실제 개행문자 사용",\n'
        '  "categories": {\n'
        '    "신선식품": ["품목명1", ...],\n'
        '    "가공식품": [...],\n'
        '    "음료/주류": [...],\n'
        '    "생활용품": [...],\n'
        '    "반려동물용품": [...],\n'
        '    "기타": [...]\n'
        "  }\n"
        "}\n\n"
        "카테고리 기준:\n"
        "- 신선식품: 채소, 과일, 육류, 수산물, 유제품, 계란\n"
        "- 가공식품: 라면, 통조림, 과자, 빵, 냉동식품, 조미료\n"
        "- 음료/주류: 음료, 물, 맥주, 소주, 커피, 차\n"
        "- 생활용품: 세제, 휴지, 청소용품, 위생용품\n"
        "- 반려동물용품: 사료, 간식, 장난감, 배변패드, 모래, 펫 관련 용품\n"
        "- 기타: 위 카테고리에 해당 없는 항목"
    )

    c = _client("__purchase_analysis__")
    reply, _ = await loop.run_in_executor(None, lambda: c.chat(prompt))

    import re
    m = re.search(r"\{[\s\S]*\}", reply)
    try:
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        parsed = {}

    summary = parsed.get("summary", "")
    cat_map = parsed.get("categories", {})
    name_to_item = {}
    for i in items:
        name_to_item.setdefault(i["raw_name"], i)

    cat_result = []
    for cat_name, names in cat_map.items():
        cat_items = [name_to_item[n] for n in names if n in name_to_item]
        if cat_items:
            cat_result.append({
                "name": cat_name,
                "amount": sum(ci["amount"] for ci in cat_items),
                "item_names": [ci["raw_name"] for ci in cat_items],
            })

    return {
        "summary": summary,
        "categories": sorted(cat_result, key=lambda x: -x["amount"]),
        "items": items,
    }


@app.post("/grocery/analyze-weekly")
async def analyze_weekly(req: PurchaseAnalysisRequest, user_id: str = Depends(get_current_user)):
    """주간 소비 패턴 비교 분석 (AI) — 최근 2주 비교."""
    _validate_date(req.start_date, "start_date")
    _validate_date(req.end_date, "end_date")
    if not state.db_enabled:
        return {"summary": ""}

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(
        None, lambda: db.get_items_by_date_range(user_id, req.start_date, req.end_date)
    )
    if not items:
        return {"summary": ""}

    from datetime import date as _date, timedelta as _td

    def week_monday(date_str: str) -> "_date":
        d = _date.fromisoformat(date_str)
        return d - _td(days=d.weekday())

    week_map: dict = {}
    for item in items:
        if not item.get("purchase_date"):
            continue
        monday = week_monday(item["purchase_date"])
        week_map.setdefault(monday, []).append(item)

    if not week_map:
        return {"summary": ""}

    sorted_weeks = sorted(week_map.keys(), reverse=True)[:2]
    weeks_info = []
    for monday in sorted_weeks:
        sunday = monday + _td(days=6)
        week_items = week_map[monday]
        total = sum(i["amount"] for i in week_items)
        weeks_info.append({
            "기간": f"{monday.isoformat()} ~ {sunday.isoformat()}",
            "총액_원": total,
            "품목": [{"품목": i["raw_name"], "금액": i["amount"], "업체": i["merchant"]} for i in week_items],
        })

    prompt = (
        "다음 주간 장보기 소비를 비교 분석해줘.\n\n"
        f"{json.dumps(weeks_info, ensure_ascii=False, indent=2)}\n\n"
        "응답은 반드시 아래 JSON 형식으로만 출력해 (다른 텍스트 없이):\n"
        "{\n"
        '  "summary": "3줄 이내 핵심 분석 (금액 포함, 줄바꿈은 실제 개행문자 사용)",\n'
        '  "weeks": [\n'
        '    {\n'
        '      "label": "이번 주",\n'
        '      "period": "YYYY-MM-DD ~ YYYY-MM-DD",\n'
        '      "total": 50000,\n'
        '      "categories": {"신선식품": 20000, "가공식품": 15000, "음료/주류": 0, "생활용품": 5000, "반려동물용품": 0, "기타": 0}\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "카테고리 기준:\n"
        "- 신선식품: 채소, 과일, 육류, 수산물, 유제품, 계란\n"
        "- 가공식품: 라면, 통조림, 과자, 빵, 냉동식품, 조미료\n"
        "- 음료/주류: 음료, 물, 맥주, 소주, 커피, 차\n"
        "- 생활용품: 세제, 휴지, 청소용품, 위생용품\n"
        "- 반려동물용품: 사료, 간식, 장난감, 배변패드, 모래, 펫 관련 용품\n"
        "- 기타: 위 카테고리에 해당 없는 항목\n\n"
        "weeks 배열은 최신 주 먼저. label은 '이번 주'/'지난 주'/'그 전 주' 등 사람이 읽기 편하게."
    )

    c = _client("__weekly_analysis__")
    reply, _ = await loop.run_in_executor(None, lambda: c.chat(prompt))

    import re as _re
    m = _re.search(r"\{[\s\S]*\}", reply)
    try:
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        parsed = {}

    return {
        "summary": parsed.get("summary", reply.strip()[:300]),
        "weeks": parsed.get("weeks", []),
    }


@app.post("/grocery/analyze-monthly")
async def analyze_monthly(req: PurchaseAnalysisRequest, user_id: str = Depends(get_current_user)):
    """월별 소비 패턴 비교 분석 (AI) — 최근 2개월 비교."""
    _validate_date(req.start_date, "start_date")
    _validate_date(req.end_date, "end_date")
    if not state.db_enabled:
        return {"summary": "", "weeks": []}

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(
        None, lambda: db.get_items_by_date_range(user_id, req.start_date, req.end_date)
    )
    if not items:
        return {"summary": "", "weeks": []}

    from datetime import date as _date

    def month_key(date_str: str) -> str:
        d = _date.fromisoformat(date_str)
        return f"{d.year}-{d.month:02d}"

    month_map: dict = {}
    for item in items:
        if not item.get("purchase_date"):
            continue
        month_map.setdefault(month_key(item["purchase_date"]), []).append(item)

    if not month_map:
        return {"summary": "", "weeks": []}

    sorted_months = sorted(month_map.keys(), reverse=True)[:2]
    labels = ["이번 달", "지난 달"]
    weeks_info = []
    for idx, key in enumerate(sorted_months):
        month_items = month_map[key]
        total = sum(i["amount"] for i in month_items)
        weeks_info.append({
            "기간": key,
            "label": labels[idx],
            "총액_원": total,
            "품목": [{"품목": i["raw_name"], "금액": i["amount"]} for i in month_items],
        })

    prompt = (
        "다음 월별 장보기 소비를 비교 분석해줘.\n\n"
        f"{json.dumps(weeks_info, ensure_ascii=False, indent=2)}\n\n"
        "응답은 반드시 아래 JSON 형식으로만 출력해 (다른 텍스트 없이):\n"
        "{\n"
        '  "summary": "3줄 이내 핵심 분석 (금액 포함, 줄바꿈은 실제 개행문자 사용)",\n'
        '  "weeks": [\n'
        '    {\n'
        '      "label": "이번 달",\n'
        '      "period": "YYYY-MM",\n'
        '      "total": 200000,\n'
        '      "categories": {"신선식품": 80000, "가공식품": 50000, "음료/주류": 0, "생활용품": 20000, "반려동물용품": 0, "기타": 0}\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "카테고리 기준:\n"
        "- 신선식품: 채소, 과일, 육류, 수산물, 유제품, 계란\n"
        "- 가공식품: 라면, 통조림, 과자, 빵, 냉동식품, 조미료\n"
        "- 음료/주류: 음료, 물, 맥주, 소주, 커피, 차\n"
        "- 생활용품: 세제, 휴지, 청소용품, 위생용품\n"
        "- 반려동물용품: 사료, 간식, 장난감, 배변패드, 모래, 펫 관련 용품\n"
        "- 기타: 위 카테고리에 해당 없는 항목\n\n"
        "weeks 배열은 최신 달 먼저. label은 '이번 달'/'지난 달'로."
    )

    c = _client("__monthly_analysis__")
    reply, _ = await loop.run_in_executor(None, lambda: c.chat(prompt))

    import re as _re
    m = _re.search(r"\{[\s\S]*\}", reply)
    try:
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        parsed = {}

    return {
        "summary": parsed.get("summary", reply.strip()[:300]),
        "weeks": parsed.get("weeks", []),
    }


@app.get("/grocery/receipt/{receipt_id}")
async def grocery_get_receipt(receipt_id: int, user_id: str = Depends(get_current_user)):
    """영수증 상세 조회 (소유자만 가능)."""
    if not state.db_enabled:
        raise HTTPException(status_code=503, detail="db_not_enabled")
    loop = asyncio.get_event_loop()
    row = await loop.run_in_executor(None, lambda: db.get_grocery_receipt_detail(receipt_id, user_id))
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return row


@app.delete("/grocery/receipt/{receipt_id}")
async def grocery_delete_receipt(receipt_id: int, user_id: str = Depends(get_current_user)):
    """영수증 삭제 (소유자만 가능)."""
    if not state.db_enabled:
        return {"ok": False, "reason": "db_not_enabled"}
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, lambda: db.delete_grocery_receipt(receipt_id, user_id))
    return {"ok": deleted, "reason": None if deleted else "not_found"}


@app.patch("/grocery/receipt/{receipt_id}")
async def grocery_update_receipt(receipt_id: int, req: GroceryUpdateRequest, user_id: str = Depends(get_current_user)):
    """영수증 메타데이터 수정 (merchant, purchase_date)."""
    if not state.db_enabled:
        return {"ok": False, "reason": "db_not_enabled"}
    loop = asyncio.get_event_loop()
    updated = await loop.run_in_executor(
        None, lambda: db.update_grocery_receipt_meta(receipt_id, req.merchant, req.purchase_date, user_id, req.total)
    )
    return {"ok": updated, "reason": None if updated else "not_found"}


@app.delete("/grocery/item/{item_id}")
async def grocery_delete_item(item_id: int, user_id: str = Depends(get_current_user)):
    """품목 삭제."""
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, lambda: db.delete_grocery_item(item_id, user_id))
    return {"ok": deleted}


@app.post("/grocery/receipt/{receipt_id}/item")
async def grocery_add_item(receipt_id: int, req: GroceryItemAddRequest, user_id: str = Depends(get_current_user)):
    """품목 추가."""
    if not state.db_enabled:
        raise HTTPException(status_code=503, detail="db_not_enabled")
    loop = asyncio.get_event_loop()
    item = await loop.run_in_executor(None, lambda: db.add_grocery_item(
        receipt_id, req.raw_name, req.qty, req.unit_price, req.amount, user_id
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="receipt_not_found")
    return item


@app.put("/grocery/item/{item_id}")
async def grocery_update_item(item_id: int, req: GroceryItemUpdateRequest, user_id: str = Depends(get_current_user)):
    """품목 수정."""
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    updated = await loop.run_in_executor(None, lambda: db.update_grocery_item(
        item_id, req.raw_name, req.qty, req.unit_price, req.amount, user_id
    ))
    return {"ok": updated}


@app.post("/grocery/suggest-names")
async def grocery_suggest_names(req: SpellCheckRequest, user_id: str = Depends(get_current_user)):
    """품목명 오타 후보 반환. {원본명: 추천명} 형태."""
    if not user_id or not state.db_enabled:
        return {"suggestions": {}}
    loop = asyncio.get_event_loop()
    known = await loop.run_in_executor(None, lambda: db.get_known_item_names(user_id))
    if not known:
        return {"suggestions": {}}
    result = {}
    for name in req.names:
        if not name or len(name) < 2:
            continue
        matches = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        if matches and matches[0] != name:
            result[name] = matches[0]
    return {"suggestions": result}


# ---------- pantry ----------

@app.get("/pantry", response_class=HTMLResponse)
async def pantry_page():
    with open("static/pantry.html", encoding="utf-8") as f:
        return f.read()


@app.get("/pantry/items")
async def pantry_list(user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: db.list_pantry(user_id))


@app.post("/pantry/items")
async def pantry_add(req: PantryAddRequest, user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        raise HTTPException(status_code=503, detail="db_not_enabled")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: db.add_pantry_item(user_id, req.raw_name, req.total_qty, req.current_qty, req.unit)
    )


@app.put("/pantry/items/{item_id}")
async def pantry_update(item_id: int, req: PantryUpdateRequest, user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    updated = await loop.run_in_executor(
        None, lambda: db.update_pantry_item(item_id, user_id, req.raw_name, req.total_qty, req.current_qty, req.unit)
    )
    return {"ok": updated}


@app.delete("/pantry/items/{item_id}")
async def pantry_delete(item_id: int, user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, lambda: db.delete_pantry_item(item_id, user_id))
    return {"ok": deleted}


# ---------- trash ----------

@app.get("/trash", response_class=HTMLResponse)
async def trash_page():
    with open("static/trash.html", encoding="utf-8") as f:
        return f.read()


@app.get("/trash/preview")
async def trash_preview(start_date: str = "", end_date: str = "", merchant: str = "", user_id: str = Depends(get_current_user)):
    _validate_date(start_date, "start_date")
    _validate_date(end_date, "end_date")
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: db.preview_for_trash(user_id, start_date, end_date, merchant))
    return result


@app.post("/trash/move")
async def trash_move(req: TrashMoveRequest, user_id: str = Depends(get_current_user)):
    _validate_date(req.start_date, "start_date")
    _validate_date(req.end_date, "end_date")
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, lambda: db.move_to_trash(user_id, req.start_date, req.end_date, req.merchant))
    return {"ok": True, "moved": count}


@app.get("/trash/items")
async def trash_list(merchant: str = "", user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: db.list_trash_items(user_id, merchant))


@app.post("/trash/restore/{trash_id}")
async def trash_restore(trash_id: int, user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    restored = await loop.run_in_executor(None, lambda: db.restore_trash_item(trash_id, user_id))
    return {"ok": restored}


@app.delete("/trash/items/{trash_id}")
async def trash_delete_item(trash_id: int, user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, lambda: db.delete_trash_item(trash_id, user_id))
    return {"ok": deleted}


@app.delete("/trash/items")
async def trash_empty(merchant: str = "", user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return {"ok": False}
    loop = asyncio.get_event_loop()
    count = await loop.run_in_executor(None, lambda: db.empty_trash(user_id, merchant))
    return {"ok": True, "deleted": count}


@app.get("/trash/merchants")
async def trash_merchants(user_id: str = Depends(get_current_user)):
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: db.list_trash_merchants(user_id))


# ---------- auth ----------

_COOKIE_OPTS = dict(httponly=True, samesite="lax", max_age=30 * 24 * 3600, path="/")

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60


class RateLimiter:
    """로그인 시도 횟수를 슬라이딩 윈도우로 제한."""
    def __init__(self, max_attempts: int = 5, window_seconds: float = 15 * 60):
        self._attempts: dict[str, list[float]] = {}
        self._max = max_attempts
        self._window = window_seconds

    def is_allowed(self, user_code: str) -> bool:
        now = time.time()
        recent = [t for t in self._attempts.get(user_code, []) if now - t < self._window]
        if recent:
            self._attempts[user_code] = recent
        else:
            self._attempts.pop(user_code, None)
        return len(recent) < self._max

    def record_failure(self, user_code: str) -> None:
        self._attempts.setdefault(user_code, []).append(time.time())

    def clear(self, user_code: str) -> None:
        self._attempts.pop(user_code, None)

    def reset_all(self) -> None:
        self._attempts.clear()


_rate_limiter = RateLimiter(max_attempts=_LOGIN_MAX_ATTEMPTS, window_seconds=_LOGIN_WINDOW_SECONDS)


@app.post("/auth/login")
async def auth_login(req: AuthRequest, response: Response):
    if not state.db_enabled:
        response.set_cookie("sid", req.user_code, **_COOKIE_OPTS)
        return {"ok": True, "user_code": req.user_code}
    if not _rate_limiter.is_allowed(req.user_code):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: db.login_user(req.user_code, req.password))
    if not ok:
        _rate_limiter.record_failure(req.user_code)
        return {"ok": False, "user_code": None, "reason": "invalid"}
    _rate_limiter.clear(req.user_code)
    token = await loop.run_in_executor(None, lambda: db.create_user_session(req.user_code))
    response.set_cookie("sid", token, **_COOKIE_OPTS)
    return {"ok": True, "user_code": req.user_code}


@app.post("/auth/register")
async def auth_register(req: RegisterRequest, response: Response):
    if not state.db_enabled:
        response.set_cookie("sid", req.user_code, **_COOKIE_OPTS)
        return {"ok": True, "user_code": req.user_code}
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: db.register_user(req.user_code, req.password))
    if not ok:
        return {"ok": False, "user_code": None, "reason": "already_exists"}
    token = await loop.run_in_executor(None, lambda: db.create_user_session(req.user_code))
    response.set_cookie("sid", token, **_COOKIE_OPTS)
    return {"ok": True, "user_code": req.user_code}


@app.post("/auth/logout")
async def auth_logout(response: Response, sid: str = Cookie(default="", alias="sid")):
    if state.db_enabled and sid:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: db.delete_user_session(sid))
    response.delete_cookie("sid", path="/")
    return {"ok": True}


@app.get("/auth/session")
async def auth_session(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}


@app.post("/push/register")
async def push_register(req: PushTokenRequest, user_id: str = Depends(get_current_user)):
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not state.db_enabled:
        return {"ok": True}
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: db.register_push_token(user_id, req.token, req.platform))
    return {"ok": True}


def _reset_for_testing(db_enabled: bool = False) -> None:
    """pytest conftest에서만 호출. 전역 state를 테스트 초기 상태로 재설정."""
    state.db_enabled = db_enabled
    state.usd_to_krw = 1300.0
    state.rate_date = "test"
    _rate_limiter.reset_all()
