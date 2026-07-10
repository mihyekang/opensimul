"""
Per-session AzureOpenAIClient cache with DB-backed conversation restore.
"""

import time

import db
from azure_openai_client import AzureOpenAIClient, ClientConfig


class SessionManager:
    def __init__(self, config: ClientConfig, db_enabled: bool = False, max_cached: int = 100):
        self._config = config
        self._db_enabled = db_enabled
        self._cache: dict[str, tuple[AzureOpenAIClient, float]] = {}
        self._max_cached = max_cached

    def get_client(self, session_id: str) -> AzureOpenAIClient:
        if session_id in self._cache:
            client, _ = self._cache[session_id]
            self._cache[session_id] = (client, time.monotonic())
            return client

        # cache miss → restore from DB
        client = AzureOpenAIClient(config=self._config)
        if self._db_enabled:
            msgs = db.load_messages(session_id, limit=self._config.max_history_turns * 2)
            for msg in msgs:
                client._conversation.append({"role": msg["role"], "content": msg["content"]})

        self._cache[session_id] = (client, time.monotonic())
        self._evict()
        return client

    def invalidate(self, session_id: str) -> None:
        """캐시에서 세션 제거 (reset 시 호출)."""
        self._cache.pop(session_id, None)

    def _evict(self) -> None:
        if len(self._cache) > self._max_cached:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]
