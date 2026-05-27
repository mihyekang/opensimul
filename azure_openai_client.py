"""
Azure OpenAI client wrapper with retry, conversation management, streaming, and token tracking.
"""

import logging
import os
import time
import warnings
from dataclasses import dataclass, field
from typing import Iterator

import httpx
from dotenv import load_dotenv
from openai import AzureOpenAI, APIConnectionError, APIStatusError, RateLimitError

load_dotenv()

logger = logging.getLogger(__name__)


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
    # USD per 1M tokens (gpt-4o-mini defaults)
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
    """Production-ready Azure OpenAI client with retry logic, conversation management, and token tracking."""

    def __init__(self, config: ClientConfig | None = None, system_prompt: str = "You are a helpful assistant."):
        self.config = config or ClientConfig()
        self._conversation: list[dict] = [{"role": "system", "content": system_prompt}]
        self._client = self._build_client()
        self._usage_history: list[TurnUsage] = []

    def _build_client(self) -> AzureOpenAI:
        if not self.config.verify_ssl:
            warnings.warn("SSL verification is disabled. Do not use in production.", stacklevel=2)

        http_client = httpx.Client(verify=self.config.verify_ssl)
        return AzureOpenAI(
            azure_endpoint=self.config.endpoint,
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            http_client=http_client,
        )

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
                logger.warning("Rate limited. Retrying in %.1fs (attempt %d/%d).", wait, attempt + 1, self.config.max_retries)
                time.sleep(wait)
                last_exc = e
            except APIConnectionError as e:
                wait = self.config.retry_delay * (2 ** attempt)
                logger.warning("Connection error. Retrying in %.1fs (attempt %d/%d).", wait, attempt + 1, self.config.max_retries)
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

    def chat(self, user_message: str) -> tuple[str, TurnUsage]:
        """Send a message and return (reply, usage). Conversation history is maintained."""
        self._conversation.append({"role": "user", "content": user_message})
        reply, usage = self._call_with_retry(self._conversation)
        self._conversation.append({"role": "assistant", "content": reply})
        self._usage_history.append(usage)
        logger.debug("Turn complete. Tokens: %d in / %d out", usage.prompt_tokens, usage.completion_tokens)
        return reply, usage

    def stream_chat(self, user_message: str) -> Iterator[str]:
        """Stream a reply token by token. Usage is recorded after the stream ends."""
        self._conversation.append({"role": "user", "content": user_message})
        collected: list[str] = []
        for token in self._stream_with_retry(self._conversation):
            collected.append(token)
            yield token
        self._conversation.append({"role": "assistant", "content": "".join(collected)})

    def analyze_image(self, image_b64: str, mime_type: str, prompt: str) -> tuple[str, TurnUsage]:
        """Send an image to the vision model. Not added to conversation history."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant that analyzes images in detail."},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        return self._call_with_retry(messages)

    def reset(self, system_prompt: str | None = None) -> None:
        """Clear conversation history, optionally replacing the system prompt."""
        system = system_prompt or self._conversation[0]["content"]
        self._conversation = [{"role": "system", "content": system}]

    @property
    def last_usage(self) -> TurnUsage | None:
        return self._usage_history[-1] if self._usage_history else None

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self._usage_history)

    @property
    def total_cost(self) -> float:
        return sum(
            u.cost(self.config.input_price_per_m, self.config.output_price_per_m)
            for u in self._usage_history
        )

    @property
    def history(self) -> list[dict]:
        return list(self._conversation)
