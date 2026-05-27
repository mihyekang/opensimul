"""
Azure OpenAI client wrapper with retry, conversation management, and streaming support.
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


class AzureOpenAIClient:
    """Production-ready Azure OpenAI client with retry logic and conversation management."""

    def __init__(self, config: ClientConfig | None = None, system_prompt: str = "You are a helpful assistant."):
        self.config = config or ClientConfig()
        self._conversation: list[dict] = [{"role": "system", "content": system_prompt}]
        self._client = self._build_client()

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

    def _call_with_retry(self, messages: list[dict]) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.config.deployment,
                    messages=messages,
                    max_completion_tokens=self.config.max_completion_tokens,
                )
                return response.choices[0].message.content
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
                )
                for chunk in stream:
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

    def chat(self, user_message: str) -> str:
        """Send a message and return the assistant reply. Conversation history is maintained."""
        self._conversation.append({"role": "user", "content": user_message})
        reply = self._call_with_retry(self._conversation)
        self._conversation.append({"role": "assistant", "content": reply})
        logger.debug("Turn complete. History length: %d messages.", len(self._conversation))
        return reply

    def stream_chat(self, user_message: str) -> Iterator[str]:
        """Stream a reply token by token. Conversation history is maintained."""
        self._conversation.append({"role": "user", "content": user_message})
        collected: list[str] = []
        for token in self._stream_with_retry(self._conversation):
            collected.append(token)
            yield token
        self._conversation.append({"role": "assistant", "content": "".join(collected)})

    def reset(self, system_prompt: str | None = None) -> None:
        """Clear conversation history, optionally replacing the system prompt."""
        system = system_prompt or self._conversation[0]["content"]
        self._conversation = [{"role": "system", "content": system}]

    @property
    def history(self) -> list[dict]:
        return list(self._conversation)
