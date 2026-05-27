"""
FastAPI web server for Azure OpenAI chat.
Run: uvicorn app:app --reload
"""

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from azure_openai_client import AzureOpenAIClient, ClientConfig
from repl import fetch_usd_to_krw

class AppState:
    client: AzureOpenAIClient
    usd_to_krw: float
    rate_date: str
    db_enabled: bool


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ClientConfig()
    state.usd_to_krw, state.rate_date = fetch_usd_to_krw(verify_ssl=config.verify_ssl)
    state.client = AzureOpenAIClient(config=config, system_prompt="You are a helpful assistant.")
    state.db_enabled = False
    if os.environ.get("POSTGRESQL_CONNECTION_STRING"):
        try:
            db.init_db()
            state.db_enabled = True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("DB 초기화 실패 — DB 저장 비활성화: %s", e)
    yield


app = FastAPI(title="Azure OpenAI Chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------- models ----------

class ChatRequest(BaseModel):
    message: str


class ResetRequest(BaseModel):
    system_prompt: str = "You are a helpful assistant."


class MemorySetRequest(BaseModel):
    key: str
    value: str


# ---------- helpers ----------

def _build_usage(usage, turn_cost_usd: float) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "turn_cost_usd": turn_cost_usd,
        "turn_cost_krw": turn_cost_usd * state.usd_to_krw,
        "total_tokens": state.client.total_tokens,
        "total_cost_usd": state.client.total_cost,
        "total_cost_krw": state.client.total_cost * state.usd_to_krw,
    }


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.post("/reset")
async def reset(req: ResetRequest):
    state.client.reset(system_prompt=req.system_prompt)
    return {"ok": True}


@app.get("/info")
async def info():
    return {
        "deployment": state.client.config.deployment,
        "usd_to_krw": state.usd_to_krw,
        "rate_date": state.rate_date,
        "total_tokens": state.client.total_tokens,
        "total_cost_usd": state.client.total_cost,
        "total_cost_krw": state.client.total_cost * state.usd_to_krw,
        "db_enabled": state.db_enabled,
        "current_turns": state.client.current_turns,
        "max_history_turns": state.client.config.max_history_turns,
    }


@app.get("/memory")
async def get_memory():
    return state.client.get_memory()


@app.post("/memory")
async def set_memory(req: MemorySetRequest):
    state.client.set_memory(req.key, req.value)
    return {"ok": True, "memory": state.client.get_memory()}


@app.delete("/memory/{key}")
async def delete_memory(key: str):
    existed = state.client.delete_memory(key)
    return {"ok": existed, "memory": state.client.get_memory()}


@app.delete("/memory")
async def clear_memory():
    state.client.clear_memory()
    return {"ok": True}


@app.post("/chat")
async def chat(req: ChatRequest):
    """Non-streaming fallback — use when corporate proxy blocks SSE."""
    loop = asyncio.get_event_loop()
    reply, usage = await loop.run_in_executor(None, lambda: state.client.chat(req.message))
    turn_cost_usd = usage.cost(state.client.config.input_price_per_m, state.client.config.output_price_per_m)
    return {"reply": reply, "usage": _build_usage(usage, turn_cost_usd)}


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE endpoint — streams tokens then sends a final [DONE] event with usage."""

    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def produce():
            try:
                for token in state.client.stream_chat(req.message):
                    loop.call_soon_threadsafe(queue.put_nowait, token)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        await loop.run_in_executor(None, produce)

        while True:
            token = await queue.get()
            if token is None:
                break
            yield f"data: {json.dumps({'token': token})}\n\n"

        last = state.client.last_usage
        if last:
            turn_cost_usd = last.cost(
                state.client.config.input_price_per_m,
                state.client.config.output_price_per_m,
            )
            yield f"data: {json.dumps({'done': True, 'usage': _build_usage(last, turn_cost_usd)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/analyze")
async def analyze(
    image: UploadFile = File(...),
    prompt: str = Form(default="이 이미지를 자세히 분석해주세요."),
):
    """Accept an image upload, run vision analysis, save result to PostgreSQL."""
    content = await image.read()
    mime_type = image.content_type or "image/jpeg"
    image_b64 = base64.b64encode(content).decode()

    loop = asyncio.get_event_loop()
    analysis, usage = await loop.run_in_executor(
        None, lambda: state.client.analyze_image(image_b64, mime_type, prompt)
    )
    turn_cost_usd = usage.cost(state.client.config.input_price_per_m, state.client.config.output_price_per_m)

    saved_id = None
    if state.db_enabled:
        row = await loop.run_in_executor(
            None,
            lambda: db.save_analysis(
                image.filename or "unknown",
                prompt,
                analysis,
                usage.prompt_tokens,
                usage.completion_tokens,
                turn_cost_usd,
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


@app.get("/analyses")
async def analyses():
    """Return recent image analysis records."""
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
