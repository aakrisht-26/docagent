"""
LLMClient -- unified LLM interface specifically for Groq Cloud.

Provider:
    Groq -- requires GROQ_API_KEY env var or groq.api_key in config.
    Uses the `openai` Python package with Groq's base_url.

Usage:
    from utils.llm_client import LLMClient
    client = LLMClient.from_config(cfg_dict)
    response = client.chat(messages=[...])
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)

# Groq defaults
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Backoff between retries: base * factor**attempt, capped, with jitter.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX_SECONDS = 8.0

T = TypeVar("T")


class LLMClient:
    """
    Client for Groq Cloud (OpenAI-compatible interface).
    """

    def __init__(
        self,
        model: str,
        api_keys: List[str] | str,
        base_url: str = GROQ_BASE_URL,
        timeout: int = 180,
        temperature: float = 0.15,
        max_total_retries: int = 4,
        cache_enabled: bool = True,
        cache_max_size: int = 128,
    ) -> None:
        self.model             = model
        self.api_keys          = [api_keys] if isinstance(api_keys, str) else api_keys
        self.base_url          = base_url
        self.timeout           = timeout
        self.temperature       = temperature
        self.max_total_retries = max_total_retries
        self._provider         = "groq"
        self._current_key_idx  = 0
        #: One OpenAI client per key index, built lazily and reused.
        self._clients: Dict[int, Any] = {}
        #: Key indices that returned HTTP 401. An invalid key stays invalid, so
        #: these are skipped on every later call instead of being retried and
        #: burning a round-trip each time.
        self._dead_key_idxs: set = set()

        if cache_enabled:
            from utils.llm_cache import LLMCache
            self._cache: Optional[Any] = LLMCache(max_size=cache_max_size)
        else:
            self._cache = None

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "LLMClient":
        """Build from config. Prioritizes GROQ_API_KEY environment variable."""
        groq_cfg = cfg.get("groq", {})

        from utils.config import resolve_groq_api_keys
        api_keys = resolve_groq_api_keys(groq_cfg)

        model = groq_cfg.get("model", GROQ_DEFAULT_MODEL)

        # Explicit `enabled: false` in config disables LLM regardless of env vars.
        # Useful in tests and offline environments.
        if not groq_cfg.get("enabled", True):
            logger.info("Groq LLM explicitly disabled via config (enabled=false).")
            return cls(model=model, api_keys=[])

        if not api_keys:
            logger.warning("No API key found for Groq (GROQ_API_KEY/S). LLM features disabled.")
            return cls(model=model, api_keys=[])

        base_url           = groq_cfg.get("base_url", GROQ_BASE_URL)
        timeout            = int(groq_cfg.get("timeout_seconds", 180))
        temp               = float(groq_cfg.get("temperature", 0.15))
        max_total_retries  = int(groq_cfg.get("max_total_retries", 4))
        cache_enabled      = bool(groq_cfg.get("cache_enabled", True))
        cache_max_size     = int(groq_cfg.get("cache_max_size", 128))

        return cls(
            model=model,
            api_keys=api_keys,
            base_url=base_url,
            timeout=timeout,
            temperature=temp,
            max_total_retries=max_total_retries,
            cache_enabled=cache_enabled,
            cache_max_size=cache_max_size,
        )

    @property
    def available(self) -> bool:
        return bool(self.api_keys)

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def provider_label(self) -> str:
        return f"groq/{self.model}" if self.available else "none"

    # ── Shared execution: timeout, retry/backoff, key rotation ────────────────

    def _client_for(self, idx: int):
        """Return (and cache) an OpenAI client bound to key `idx`."""
        import openai

        if idx not in self._clients:
            self._clients[idx] = openai.OpenAI(
                api_key=self.api_keys[idx],
                base_url=self.base_url,
                timeout=float(self.timeout),
            )
        return self._clients[idx]

    def _live_key_idxs(self) -> List[int]:
        """Key indices not yet known to be invalid, starting at the current one."""
        n = len(self.api_keys)
        order = [(self._current_key_idx + i) % n for i in range(n)]
        return [i for i in order if i not in self._dead_key_idxs]

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        """Exponential backoff with jitter, capped."""
        delay = min(_BACKOFF_BASE_SECONDS * (_BACKOFF_FACTOR ** attempt), _BACKOFF_MAX_SECONDS)
        return delay * (0.5 + random.random() / 2)  # 50-100% of the delay

    def _run_with_rotation(self, operation: Callable[[Any], T], what: str) -> Optional[T]:
        """
        Run `operation(client)` with timeout, retry/backoff and key rotation.

        This is the single place where API failure handling lives. Every call
        site — chat completions and audio transcription alike — goes through it,
        so the retry policy cannot drift between skills.

        Policy:
          - HTTP 401  → the key is permanently invalid. Record it in
            `_dead_key_idxs` so it is skipped for the rest of the process, and
            move to the next key immediately (no backoff; the key will never
            start working).
          - HTTP 429 / rate limit → transient. Rotate to the next live key and
            back off before retrying.
          - Connection / timeout / 5xx → transient. Retry with backoff.
          - Anything else → not retryable; give up and return None.

        Returns the operation's result, or None if every avenue is exhausted.
        """
        try:
            import openai
        except ImportError:
            logger.error("OpenAI package not installed.")
            return None

        live = self._live_key_idxs()
        if not live:
            logger.error(
                f"{what}: all {len(self.api_keys)} Groq key(s) are known-invalid (401). "
                "Check GROQ_API_KEYS."
            )
            return None

        attempts = max(self.max_total_retries, len(live))
        rate_hits = 0
        transient_hits = 0

        for attempt in range(attempts):
            live = self._live_key_idxs()
            if not live:
                logger.error(f"{what}: every key returned 401; giving up.")
                return None

            # `_live_key_idxs()` is ordered starting at the rotation pointer, so
            # live[0] is the next usable key. Indexing by attempt number instead
            # would skip keys, because the list shrinks as keys are retired.
            idx = live[0]
            key_label = f"key {idx + 1}/{len(self.api_keys)}"

            def _rotate() -> None:
                self._current_key_idx = (idx + 1) % len(self.api_keys)

            try:
                return operation(self._client_for(idx))

            except openai.RateLimitError:
                rate_hits += 1
                delay = self._backoff_seconds(attempt)
                logger.warning(
                    f"{what}: rate limit on {key_label} (hit #{rate_hits}); "
                    f"rotating and retrying in {delay:.1f}s "
                    f"[attempt {attempt + 1}/{attempts}]"
                )
                _rotate()
                time.sleep(delay)

            except openai.APIStatusError as exc:
                if exc.status_code == 401:
                    self._dead_key_idxs.add(idx)
                    self._clients.pop(idx, None)
                    logger.warning(
                        f"{what}: {key_label} rejected as invalid (401). "
                        f"Retiring it for this process; "
                        f"{len(self.api_keys) - len(self._dead_key_idxs)} key(s) remain."
                    )
                    _rotate()
                    continue  # another key may work; no point backing off
                if exc.status_code >= 500:
                    transient_hits += 1
                    delay = self._backoff_seconds(attempt)
                    logger.warning(
                        f"{what}: server error {exc.status_code} on {key_label}; "
                        f"retrying in {delay:.1f}s [attempt {attempt + 1}/{attempts}]"
                    )
                    _rotate()
                    time.sleep(delay)
                    continue
                logger.warning(f"{what}: API status error ({exc.status_code}): {exc.message}")
                return None

            except (openai.APIConnectionError, openai.APITimeoutError) as exc:
                transient_hits += 1
                delay = self._backoff_seconds(attempt)
                logger.warning(
                    f"{what}: {type(exc).__name__} on {key_label}; "
                    f"retrying in {delay:.1f}s [attempt {attempt + 1}/{attempts}]"
                )
                _rotate()
                time.sleep(delay)

            except Exception as exc:
                logger.warning(f"{what}: non-retryable failure: {type(exc).__name__}: {exc}")
                return None

        logger.error(
            f"{what}: giving up after {attempts} attempt(s) "
            f"(rate-limited: {rate_hits}, transient: {transient_hits}, "
            f"invalid keys retired: {len(self._dead_key_idxs)})."
        )
        return None

    # ── Public operations ─────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: int = 3000,
    ) -> Optional[str]:
        if not self.available:
            return None

        temp = temperature if temperature is not None else self.temperature

        # Check cache before making an API call
        if self._cache is not None:
            cached = self._cache.get(self.model, messages, temp, max_tokens)
            if cached is not None:
                logger.debug("LLM cache hit — skipping API call")
                return cached

        def _do(client) -> Optional[str]:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temp,
                max_tokens=max_tokens,
            )
            if not response.choices:
                logger.warning("Groq API returned an empty choices array.")
                return None
            content = response.choices[0].message.content
            return content.strip() if content else None

        result = self._run_with_rotation(_do, what="chat")

        if result and self._cache is not None:
            self._cache.set(self.model, messages, temp, max_tokens, result)
        return result

    def transcribe(
        self,
        audio_path: Any,
        model: str,
        response_format: str = "text",
    ) -> Optional[str]:
        """
        Transcribe an audio file, reusing the same timeout / retry / rotation
        policy as `chat()`.

        `model` is supplied by the caller rather than defaulted here, so the
        transcription model stays an explicit choice at the call site.

        Not cached: audio payloads are large and never repeat within a run.
        """
        if not self.available:
            return None

        def _do(client) -> Optional[str]:
            # Reopened per attempt: a retry must re-read from the start, and a
            # consumed file handle would otherwise upload zero bytes.
            with open(audio_path, "rb") as fh:
                response = client.audio.transcriptions.create(
                    model=model,
                    file=fh,
                    response_format=response_format,
                )
            text = response if isinstance(response, str) else getattr(response, "text", None)
            return text.strip() if isinstance(text, str) else None

        return self._run_with_rotation(_do, what="transcribe")

    def __repr__(self) -> str:
        return f"<LLMClient provider=groq model={self.model!r}>"
