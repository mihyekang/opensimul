"""
FastAPI web server for Azure OpenAI chat.
Run: uvicorn app:app --reload
"""

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from azure_openai_client import AzureOpenAIClient, ClientConfig
from repl import fetch_usd_to_krw


class AppState:
    client: AzureOpenAIClient
    usd_to_krw: float
    rate_date: str


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ClientConfig()
    state.usd_to_krw, state.rate_date = fetch_usd_to_krw(verify_ssl=config.verify_ssl)
    state.client = AzureOpenAIClient(config=config, system_prompt="You are a helpful assistant.")
    yield


app = FastAPI(title="Azure OpenAI Chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------- models ----------

class ChatRequest(BaseModel):
    message: str


class ResetRequest(BaseModel):
    system_prompt: str = "You are a helpful assistant."


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
    }


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
            yield f"data: {json.dumps({'done': True, 'usage': {'prompt_tokens': last.prompt_tokens, 'completion_tokens': last.completion_tokens, 'turn_cost_usd': turn_cost_usd, 'turn_cost_krw': turn_cost_usd * state.usd_to_krw, 'total_tokens': state.client.total_tokens, 'total_cost_usd': state.client.total_cost, 'total_cost_krw': state.client.total_cost * state.usd_to_krw}})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
