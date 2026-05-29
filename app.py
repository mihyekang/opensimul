"""
FastAPI web server for Azure OpenAI chat.
Run: uvicorn app:app --reload
"""

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, File, Form, UploadFile, Body
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from azure_openai_client import ClientConfig
from grocery_pass1 import extract_pass1_bytes, validate
from repl import fetch_usd_to_krw
from session_manager import SessionManager

logger = logging.getLogger(__name__)
SESSION_RETENTION_DAYS = 7


class AppState:
    session_manager: SessionManager
    usd_to_krw: float
    rate_date: str
    db_enabled: bool


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ClientConfig()
    state.usd_to_krw, state.rate_date = fetch_usd_to_krw(verify_ssl=config.verify_ssl)
    state.db_enabled = False

    if os.environ.get("POSTGRESQL_CONNECTION_STRING"):
        try:
            db.init_db()
            state.db_enabled = True
            loop = asyncio.get_event_loop()
            deleted = await loop.run_in_executor(
                None, lambda: db.cleanup_old_sessions(SESSION_RETENTION_DAYS)
            )
            logger.info("만료 세션 %d개 정리 완료", deleted)
        except Exception as e:
            logger.warning("DB 초기화 실패 — DB 저장 비활성화: %s", e)

    state.session_manager = SessionManager(config, db_enabled=state.db_enabled)
    yield


app = FastAPI(title="Azure OpenAI Chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------- models ----------

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


# ---------- helpers ----------

def _client(session_id: str):
    return state.session_manager.get_client(session_id)


def _build_purchase_summary(rows: list[dict]) -> str:
    """최근 구매 이력을 챗봇 시스템 프롬프트용 컴팩트 텍스트로 변환."""
    if not rows:
        return ""
    lines = ["[최근 구매 내역]"]
    for r in rows[:10]:
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

@app.get("/", response_class=HTMLResponse)
async def index():
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

async def _inject_grocery_context(c) -> None:
    """최근 7일 구매 이력을 클라이언트 transient context에 주입."""
    if not state.db_enabled:
        return
    loop = asyncio.get_event_loop()
    try:
        rows = await loop.run_in_executor(None, lambda: db.get_recent_groceries(7))
        summary = _build_purchase_summary(rows)
        if summary:
            c.set_transient("grocery", summary)
        else:
            c.clear_transient("grocery")
    except Exception:
        logger.exception("구매 이력 조회 실패")


@app.post("/chat")
async def chat(req: ChatRequest):
    c = _client(req.session_id)
    await _inject_grocery_context(c)
    loop = asyncio.get_event_loop()
    reply, usage = await loop.run_in_executor(None, lambda: c.chat(req.message))
    turn_cost_usd = usage.cost(c.config.input_price_per_m, c.config.output_price_per_m)
    await _save_turn(req.session_id, req.message, reply)
    return {"reply": reply, "usage": _build_usage(c, usage, turn_cost_usd)}


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    c = _client(req.session_id)
    await _inject_grocery_context(c)

    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        collected: list[str] = []

        def produce():
            try:
                for token in c.stream_chat(req.message):
                    loop.call_soon_threadsafe(queue.put_nowait, token)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, produce)

        while True:
            token = await queue.get()
            if token is None:
                break
            collected.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"

        assistant_reply = "".join(collected)
        await _save_turn(req.session_id, req.message, assistant_reply)

        last = c.last_usage
        if last:
            turn_cost_usd = last.cost(c.config.input_price_per_m, c.config.output_price_per_m)
            yield f"data: {json.dumps({'done': True, 'usage': _build_usage(c, last, turn_cost_usd)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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
    """Pass 1: 영수증 이미지 → JSON 추출 + 검증."""
    content = await image.read()
    mime_type = image.content_type or "image/jpeg"
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


@app.post("/grocery/save")
async def grocery_save(req: GrocerySaveRequest):
    """Pass 1 추출 결과를 DB에 저장."""
    if not state.db_enabled:
        return {"ok": False, "reason": "db_not_enabled"}
    loop = asyncio.get_event_loop()
    receipt_id = await loop.run_in_executor(
        None, lambda: db.save_grocery_receipt(req.result)
    )
    return {"ok": True, "receipt_id": receipt_id}


@app.get("/grocery/history")
async def grocery_history(days: int = 30):
    """최근 N일 구매 이력 반환."""
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: db.list_grocery_receipts(days))
    for r in rows:
        if r.get("purchase_date"):
            r["purchase_date"] = r["purchase_date"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return rows


@app.get("/grocery/debug")
async def grocery_debug():
    """DB 연결 상태 및 최근 영수증 확인용 엔드포인트."""
    if not state.db_enabled:
        return {"db_enabled": False}
    loop = asyncio.get_event_loop()
    try:
        rows = await loop.run_in_executor(None, lambda: db.list_grocery_receipts(30))
        for r in rows:
            if r.get("purchase_date"):
                r["purchase_date"] = r["purchase_date"].isoformat()
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
        return {"db_enabled": True, "receipt_count": len(rows), "receipts": rows}
    except Exception as e:
        logger.exception("grocery_debug 오류")
        return {"db_enabled": True, "error": str(e)}


@app.get("/analyses")
async def analyses():
    if not state.db_enabled:
        return []
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, db.list_analyses)
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        if r.get("cost_usd"):
            r["cost_usd"] = float(r["cost_usd"])
    return rows
