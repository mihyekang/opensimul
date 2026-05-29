"""
Azure OpenAI client wrapper with retry, conversation management, streaming,
token tracking, sliding-window history, and persistent memory.
"""

import json
import logging
import os
import time
import warnings
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator

import httpx
from dotenv import load_dotenv
from openai import AzureOpenAI, APIConnectionError, APIStatusError, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)

# memory.json은 Azure App Service에서 /home이 영구 스토리지
MEMORY_FILE = os.environ.get("MEMORY_FILE", "memory.json")


@dataclass
class ClientConfig:
    endpoint: str = field(default_factory=lambda: os.environ["AZURE_OPENAI_ENDPOINT"])
    api_key: str = field(default_factory=lambda: os.environ["AZURE_OPENAI_API_KEY"])
    deployment: str = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"))
    api_version: str = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"))
    verify_ssl: bool = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_VERIFY_SSL", "true").lower() != "false")
    max_retries: int = 3
    retry_delay: float = 1.0
    max_completion_tokens: int = 16384
    max_history_turns: int = field(default_factory=lambda: int(os.environ.get("MAX_HISTORY_TURNS", "20")))
    input_price_per_m: float = field(default_factory=lambda: float(os.environ.get("PRICE_INPUT_PER_M", "0.15")))
    output_price_per_m: float = field(default_factory=lambda: float(os.environ.get("PRICE_OUTPUT_PER_M", "0.60")))


@dataclass
class TurnUsage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost(self, input_price_per_m: float, output_price_per_m: float) -> float:
        return (self.prompt_tokens * input_price_per_m + self.completion_tokens * output_price_per_m) / 1_000_000


class AzureOpenAIClient:
    """Azure OpenAI client with sliding-window history, memory injection, and token tracking."""

    def __init__(self, config: ClientConfig | None = None, base_system_prompt: str = "You are a helpful assistant."):
        self.config = config or ClientConfig()
        self._base_system_prompt = base_system_prompt
        self._memory: dict[str, str] = _load_memory()
        self._transient: dict[str, str] = {}
        self._conversation: list[dict] = [{"role": "system", "content": self._build_system_prompt()}]
        self._client = self._build_client()
        self._usage_history: list[TurnUsage] = []

    # ── system prompt ──────────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        parts = [self._base_system_prompt, f"오늘 날짜: {date.today()}"]
        if self._memory:
            lines = "\n".join(f"- {k}: {v}" for k, v in self._memory.items())
            parts.append(f"[사용자 정보]\n{lines}")
        if self._transient:
            lines = "\n".join(f"{v}" for v in self._transient.values())
            parts.append(lines)
        return "\n\n".join(parts)

    def _refresh_system_prompt(self) -> None:
        self._conversation[0]["content"] = self._build_system_prompt()

    # ── memory ─────────────────────────────────────────────────────────────────

    def set_memory(self, key: str, value: str) -> None:
        self._memory[key] = value
        _save_memory(self._memory)
        self._refresh_system_prompt()

    def delete_memory(self, key: str) -> bool:
        existed = key in self._memory
        self._memory.pop(key, None)
        _save_memory(self._memory)
        self._refresh_system_prompt()
        return existed

    def clear_memory(self) -> None:
        self._memory.clear()
        _save_memory(self._memory)
        self._refresh_system_prompt()

    def get_memory(self) -> dict[str, str]:
        return dict(self._memory)

    # ── transient context (not persisted) ─────────────────────────────────────

    def set_transient(self, key: str, value: str) -> None:
        self._transient[key] = value
        self._refresh_system_prompt()

    def clear_transient(self, key: str) -> None:
        self._transient.pop(key, None)
        self._refresh_system_prompt()

    # ── sliding window ─────────────────────────────────────────────────────────

    def _trim_history(self) -> None:
        """시스템 프롬프트 + 최근 max_history_turns 쌍만 유지."""
        max_msgs = self.config.max_history_turns * 2 + 1
        if len(self._conversation) > max_msgs:
            self._conversation = [self._conversation[0]] + self._conversation[-(max_msgs - 1):]

    @property
    def current_turns(self) -> int:
        return (len(self._conversation) - 1) // 2

    # ── http client ────────────────────────────────────────────────────────────

    def _build_client(self) -> AzureOpenAI:
        if not self.config.verify_ssl:
            warnings.warn("SSL verification is disabled. Do not use in production.", stacklevel=2)
        return AzureOpenAI(
            azure_endpoint=self.config.endpoint,
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            http_client=httpx.Client(verify=self.config.verify_ssl),
        )

    # ── retry wrappers ─────────────────────────────────────────────────────────

    def _call_with_retry(self, messages: list[dict]) -> tuple[str, TurnUsage]:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.config.deployment,
                    messages=messages,
                    max_completion_tokens=self.config.max_completion_tokens,
                )
                usage = TurnUsage(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )
                return response.choices[0].message.content, usage
            except RateLimitError as e:
                wait = self.config.retry_delay * (2 ** attempt)
                logger.warning("Rate limited. Retrying in %.1fs (%d/%d).", wait, attempt + 1, self.config.max_retries)
                time.sleep(wait)
                last_exc = e
            except APIConnectionError as e:
                wait = self.config.retry_delay * (2 ** attempt)
                logger.warning("Connection error. Retrying in %.1fs (%d/%d).", wait, attempt + 1, self.config.max_retries)
                time.sleep(wait)
                last_exc = e
            except APIStatusError as e:
                logger.error("API error %d: %s", e.status_code, e.message)
                raise
        raise last_exc  # type: ignore[misc]

    def _stream_with_retry(self, messages: list[dict]) -> Iterator[str]:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                stream = self._client.chat.completions.create(
                    model=self.config.deployment,
                    messages=messages,
                    max_completion_tokens=self.config.max_completion_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                for chunk in stream:
                    if chunk.usage:
                        self._usage_history.append(TurnUsage(
                            prompt_tokens=chunk.usage.prompt_tokens,
                            completion_tokens=chunk.usage.completion_tokens,
                        ))
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except RateLimitError as e:
                wait = self.config.retry_delay * (2 ** attempt)
                logger.warning("Rate limited. Retrying in %.1fs.", wait)
                time.sleep(wait)
                last_exc = e
            except APIConnectionError as e:
                wait = self.config.retry_delay * (2 ** attempt)
                logger.warning("Connection error. Retrying in %.1fs.", wait)
                time.sleep(wait)
                last_exc = e
            except APIStatusError as e:
                logger.error("API error %d: %s", e.status_code, e.message)
                raise
        raise last_exc  # type: ignore[misc]

    # ── public chat API ────────────────────────────────────────────────────────

    def chat(self, user_message: str) -> tuple[str, TurnUsage]:
        self._conversation.append({"role": "user", "content": user_message})
        reply, usage = self._call_with_retry(self._conversation)
        self._conversation.append({"role": "assistant", "content": reply})
        self._usage_history.append(usage)
        self._trim_history()
        return reply, usage

    def stream_chat(self, user_message: str) -> Iterator[str]:
        self._conversation.append({"role": "user", "content": user_message})
        collected: list[str] = []
        for token in self._stream_with_retry(self._conversation):
            collected.append(token)
            yield token
        self._conversation.append({"role": "assistant", "content": "".join(collected)})
        self._trim_history()

    def analyze_image(self, image_b64: str, mime_type: str, prompt: str) -> tuple[str, TurnUsage]:
        """이미지 분석 — 대화 이력에 추가하지 않음."""
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        return self._call_with_retry(messages)

    def reset(self, base_system_prompt: str | None = None) -> None:
        if base_system_prompt:
            self._base_system_prompt = base_system_prompt
        self._conversation = [{"role": "system", "content": self._build_system_prompt()}]

    # ── stats ──────────────────────────────────────────────────────────────────

    @property
    def last_usage(self) -> TurnUsage | None:
        return self._usage_history[-1] if self._usage_history else None

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self._usage_history)

    @property
    def total_cost(self) -> float:
        return sum(u.cost(self.config.input_price_per_m, self.config.output_price_per_m) for u in self._usage_history)

    @property
    def history(self) -> list[dict]:
        return list(self._conversation)


# ── memory persistence ─────────────────────────────────────────────────────────

def _load_memory() -> dict[str, str]:
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_memory(memory: dict[str, str]) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("메모리 저장 실패: %s", e)
