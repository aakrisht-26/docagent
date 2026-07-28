"""
LLMCache — content-addressed, in-memory LRU cache for LLM responses.

Keyed by sha256(model + serialised messages + temperature + max_tokens).
Two identical LLM calls (same model, messages, and params) return the cached
response without hitting the API. This eliminates redundant cost and latency
when the same document chunk is processed more than once (e.g. on re-upload).

Usage:
    cache = LLMCache(max_size=128)
    cached = cache.get(model, messages, temperature, max_tokens)
    if cached is None:
        result = api_call(...)
        cache.set(model, messages, temperature, max_tokens, result)
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional


class LLMCache:
    """Thread-unsafe in-process LRU cache for LLM chat responses."""

    def __init__(self, max_size: int = 128) -> None:
        self._max_size = max(1, max_size)
        self._store: OrderedDict[str, str] = OrderedDict()
        self.hits   = 0
        self.misses = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        key = self._make_key(model, messages, temperature, max_tokens)
        if key in self._store:
            self._store.move_to_end(key)
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return None

    def set(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response: str,
    ) -> None:
        key = self._make_key(model, messages, temperature, max_tokens)
        if key in self._store:
            self._store.move_to_end(key)
        else:
            if len(self._store) >= self._max_size:
                self._store.popitem(last=False)  # evict LRU entry
            self._store[key] = response

    def clear(self) -> None:
        self._store.clear()
        self.hits = self.misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    # ── Internals ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = json.dumps(
            {"model": model, "messages": messages,
             "temperature": temperature, "max_tokens": max_tokens},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
