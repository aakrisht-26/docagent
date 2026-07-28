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

from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Groq defaults
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


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
        self._client           = None
        self._current_key_idx  = 0

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

        try:
            import openai
        except ImportError:
            logger.error("OpenAI package not installed.")
            return None

        num_keys    = len(self.api_keys)
        rate_hits   = 0  # rate-limit hits
        invalid_keys = 0  # 401 invalid-key hits

        for _ in range(num_keys):
            try:
                if self._client is None:
                    self._client = openai.OpenAI(
                        api_key=self.api_keys[self._current_key_idx],
                        base_url=self.base_url,
                        timeout=float(self.timeout),
                    )

                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tokens,
                )
                if not response.choices:
                    logger.warning("Groq API returned an empty choices array.")
                    return None
                content = response.choices[0].message.content
                result  = content.strip() if content else None
                if result and self._cache is not None:
                    self._cache.set(self.model, messages, temp, max_tokens, result)
                return result

            except openai.RateLimitError:
                rate_hits += 1
                logger.warning(
                    f"Rate limit on key {self._current_key_idx + 1}/{num_keys} "
                    f"(hit #{rate_hits}) — switching to next key instantly."
                )
                self._current_key_idx = (self._current_key_idx + 1) % num_keys
                self._client = None  # force recreation with the new key

            except openai.APIStatusError as exc:
                if exc.status_code == 401:
                    invalid_keys += 1
                    logger.warning(
                        f"Invalid API key {self._current_key_idx + 1}/{num_keys} (401) "
                        f"— switching to next key instantly."
                    )
                    self._current_key_idx = (self._current_key_idx + 1) % num_keys
                    self._client = None
                else:
                    logger.warning(f"Groq API status error ({exc.status_code}): {exc.message}")
                    return None

            except Exception as exc:
                logger.warning(f"Groq API call failed: {exc}")
                return None

        logger.error(
            f"All {num_keys} Groq key(s) exhausted "
            f"(rate-limited: {rate_hits}, invalid: {invalid_keys}). Giving up."
        )
        return None

    def __repr__(self) -> str:
        return f"<LLMClient provider=groq model={self.model!r}>"
