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
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)


# ── Usage accounting ──────────────────────────────────────────────────────────
#
# Skills each build their own LLMClient, so per-run totals are accumulated in a
# module-level record rather than per instance. The agent resets it at the start
# of a pipeline run and reads it at the end.

@dataclass
class UsageTotals:
    """Token and call counts accumulated since the last reset."""
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    transcriptions: int = 0
    per_model: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record_chat(self, model: str, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        slot = self.per_model.setdefault(model, {"calls": 0, "prompt": 0, "completion": 0})
        slot["calls"] += 1
        slot["prompt"] += prompt
        slot["completion"] += completion


_USAGE = UsageTotals()

# A second accumulator that is NEVER reset, so it measures the whole process
# rather than the last pipeline run. On a public deployment the per-run figure
# answers "what did that document cost"; this one answers "how hard is this
# being hit", which is the question that matters when a shared API key is
# approaching a daily limit. Both are fed from the same three call sites, so
# they cannot drift apart.
_USAGE_TOTAL = UsageTotals()
_PROCESS_START = time.time()


def reset_usage() -> None:
    """Zero the per-run usage counters. Called at the start of a pipeline run.

    Deliberately does NOT touch the process-lifetime totals.
    """
    global _USAGE
    _USAGE = UsageTotals()


def get_usage() -> UsageTotals:
    """Return the usage accumulated since the last `reset_usage()`."""
    return _USAGE


def get_process_usage() -> UsageTotals:
    """Return usage accumulated since the process started. Never reset."""
    return _USAGE_TOTAL


def process_uptime_seconds() -> float:
    """Seconds since this process started, for rate-per-hour reporting."""
    return time.time() - _PROCESS_START


def estimate_cost_usd(usage: UsageTotals, pricing: Optional[Dict[str, Any]] = None) -> float:
    """
    Estimate spend in USD from token counts.

    Rates come from `groq.pricing` in configs/default.yaml and are **estimates**
    that must be checked against current published pricing — they are not billed
    figures. Audio transcription is excluded: Whisper is billed per second of
    audio, not per token, and the text response carries no usage object.
    """
    pricing = pricing or {}
    in_rate = float(pricing.get("input_usd_per_million", 0.0))
    out_rate = float(pricing.get("output_usd_per_million", 0.0))
    return (usage.prompt_tokens / 1_000_000 * in_rate
            + usage.completion_tokens / 1_000_000 * out_rate)

# Groq defaults
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

# Backoff between retries: base * factor**attempt, capped, with jitter.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX_SECONDS = 8.0

# A 429 whose retry window is longer than this means the key is exhausted for
# the window rather than briefly throttled. Retrying it is pointless, so it is
# parked and the next key is used immediately.
_DEFAULT_PARK_THRESHOLD_SECONDS = 15.0

# Smallest request worth un-parking a key for, in tokens. Day-window parks are
# scaled to the time needed to free this much budget rather than the full
# retry-after, which answers only for the (often much larger) request that was
# refused. See LLMClient._proportional_park_seconds.
_DEFAULT_PARK_MIN_REQUEST_TOKENS = 500

T = TypeVar("T")

# ── Process-wide rate-limit state, shared across LLMClient instances ──────────
#
# Keyed by the tuple of API keys, because index-based state only means anything
# for clients configured with the same key list in the same order. Every client
# built from those keys receives the SAME container objects, so mutations made
# by one are immediately visible to all.
#
# Keys are used only as an in-memory dictionary key and are never logged.
_SHARED_KEY_STATE: Dict[tuple, Dict[str, Any]] = {}
_SHARED_STATE_LOCK = threading.Lock()


def _shared_key_state(api_keys: List[str]) -> Dict[str, Any]:
    """Return the shared rate-limit state for this exact key list, creating it once."""
    identity = tuple(api_keys)
    with _SHARED_STATE_LOCK:
        state = _SHARED_KEY_STATE.get(identity)
        if state is None:
            state = {
                "dead": set(),        # key indices retired by 401
                "parked": {},         # key index -> monotonic deadline
                "headroom": {},       # key index -> last seen header figures
                "daily": {},          # key index -> figures inferred from a 429
                "pointer": [0],       # rotation cursor, boxed so it is mutable
            }
            _SHARED_KEY_STATE[identity] = state
        return state

# Groq durations look like "28m3.936s", "1h16m19.2s", "1m26.4s", "185ms", "2.5s".
_DURATION_RE = re.compile(
    r"(?:(?P<h>[\d.]+)h)?(?:(?P<m>[\d.]+)m(?!s))?(?:(?P<s>[\d.]+)s)?(?:(?P<ms>[\d.]+)ms)?$"
)


def parse_duration_seconds(text: Optional[str]) -> Optional[float]:
    """
    Parse a Groq duration string into seconds.

    Handles the compound forms Groq uses in both the `retry-after` header and
    the 429 message body: "28m3.936s", "1h16m19.2s", "1m26.4s", "185ms", "7.5s",
    and a bare number of seconds such as "60".
    """
    if not text:
        return None
    text = text.strip()

    # Bare seconds, as the retry-after header usually gives.
    try:
        return float(text)
    except ValueError:
        pass

    # Milliseconds-only, which the compound regex would otherwise read as minutes.
    ms_only = re.fullmatch(r"([\d.]+)ms", text)
    if ms_only:
        return float(ms_only.group(1)) / 1000.0

    match = _DURATION_RE.fullmatch(text)
    if not match or not any(match.groupdict().values()):
        return None
    parts = match.groupdict()
    total = 0.0
    if parts.get("h"):
        total += float(parts["h"]) * 3600
    if parts.get("m"):
        total += float(parts["m"]) * 60
    if parts.get("s"):
        total += float(parts["s"])
    if parts.get("ms"):
        total += float(parts["ms"]) / 1000.0
    return total


def parse_rate_limit_error(message: str) -> Dict[str, Any]:
    """
    Extract the structured facts from a Groq 429 message.

    Groq names the exact limit that tripped, which matters because the four
    limits behave very differently:

        RPM / TPM  — per-minute windows; a short wait clears them.
        RPD / TPD  — per-day windows; the key is done until the day rolls over.

    Note that TPD is NOT reported in any `x-ratelimit-*` header — it is only
    visible here, in the body of a refusal.
    """
    info: Dict[str, Any] = {}
    org = re.search(r"in organization `([^`]+)`", message)
    if org:
        info["organization"] = org.group(1)
    kind = re.search(r"on (tokens|requests) per (day|minute) \((RPD|TPD|RPM|TPM)\)", message)
    if kind:
        info["limit_type"] = kind.group(3)
        info["window"] = kind.group(2)
    nums = re.search(r"Limit (\d+), Used (\d+), Requested (\d+)", message)
    if nums:
        info["limit"] = int(nums.group(1))
        info["used"] = int(nums.group(2))
        info["requested"] = int(nums.group(3))
    retry = re.search(r"try again in ([\dhms.]+)", message)
    if retry:
        # The character class also swallows the sentence-ending period, which
        # would otherwise make the duration unparseable and silently disable
        # parking. Strip trailing dots before parsing.
        text = retry.group(1).rstrip(".")
        info["retry_after_text"] = text
        info["retry_after_seconds"] = parse_duration_seconds(text)
    return info


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
        park_threshold_seconds: float = _DEFAULT_PARK_THRESHOLD_SECONDS,
        park_min_request_tokens: int = _DEFAULT_PARK_MIN_REQUEST_TOKENS,
    ) -> None:
        self.park_threshold    = float(park_threshold_seconds)
        self._park_min_request = int(park_min_request_tokens)
        self.model             = model
        self.api_keys          = [api_keys] if isinstance(api_keys, str) else api_keys
        self.base_url          = base_url
        self.timeout           = timeout
        self.temperature       = temperature
        self.max_total_retries = max_total_retries
        self._provider         = "groq"
        #: One OpenAI client per key index, built lazily and reused. NOT shared:
        #: these hold sockets and are cheap to rebuild, and sharing them across
        #: instances would entangle connection lifecycles.
        self._clients: Dict[int, Any] = {}

        # ── Shared, process-wide rate-limit state ─────────────────────────
        # Rate-limit state belongs to the API KEY, not to whichever object
        # happens to be holding it. Every skill builds its own LLMClient — some
        # per instance, some on every call — so per-instance tables meant a key
        # parked during summarisation was retried immediately by the next stage,
        # and the "N key(s) still usable" count reset on every stage boundary.
        #
        # The containers below are looked up by the key list and shared by every
        # client using those keys, so parking, 401 retirement, the rotation
        # pointer and observed limits all persist across stages. Skills keep
        # their own client objects and their existing lifecycles; only the state
        # is shared.
        shared = _shared_key_state(self.api_keys)
        #: Key indices that returned HTTP 401. An invalid key stays invalid, so
        #: these are skipped on every later call instead of being retried and
        #: burning a round-trip each time.
        self._dead_key_idxs: set = shared["dead"]
        #: Key index -> monotonic deadline until which the key is rate-limited.
        #: Unlike 401 this expires: daily and per-minute windows do reset, so a
        #: parked key returns to the rotation once its window has passed.
        self._parked_until: Dict[int, float] = shared["parked"]
        #: Last seen rate-limit headroom per key, for diagnostics.
        self._headroom: Dict[int, Dict[str, Any]] = shared["headroom"]
        #: Per-key daily-limit facts learned from a 429 body. Groq reports no
        #: header for tokens-per-day, so the only place TPD is ever stated is in
        #: the body of a refusal. What is cached here is therefore an INFERENCE
        #: from the last refusal, not a live counter, and is labelled as such
        #: wherever it is surfaced. Keys never refused stay absent -> "unknown".
        self._observed_daily: Dict[int, Dict[str, Any]] = shared["daily"]
        #: Rotation pointer, shared so a later stage resumes where the previous
        #: one left off instead of restarting at key 1 every time.
        self._pointer: List[int] = shared["pointer"]

        if cache_enabled:
            from utils.llm_cache import LLMCache
            self._cache: Optional[Any] = LLMCache(max_size=cache_max_size)
        else:
            self._cache = None

    # ── Shared rotation pointer ───────────────────────────────────────
    # Exposed as a property so existing reads and assignments of
    # `self._current_key_idx` keep working unchanged while the value actually
    # lives in the shared state.

    @property
    def _current_key_idx(self) -> int:
        return self._pointer[0]

    @_current_key_idx.setter
    def _current_key_idx(self, value: int) -> None:
        self._pointer[0] = value

    @staticmethod
    def reset_shared_state() -> None:
        """
        Clear all process-wide key state.

        Intended for tests and for the rare case of reloading configuration in a
        long-lived process. Without this, state legitimately persists for the
        lifetime of the process, which is the entire point.
        """
        with _SHARED_STATE_LOCK:
            _SHARED_KEY_STATE.clear()

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
        park_threshold     = float(groq_cfg.get("park_threshold_seconds",
                                                _DEFAULT_PARK_THRESHOLD_SECONDS))
        park_min_tokens    = int(groq_cfg.get("park_min_request_tokens",
                                              _DEFAULT_PARK_MIN_REQUEST_TOKENS))

        return cls(
            model=model,
            api_keys=api_keys,
            base_url=base_url,
            timeout=timeout,
            temperature=temp,
            max_total_retries=max_total_retries,
            cache_enabled=cache_enabled,
            cache_max_size=cache_max_size,
            park_threshold_seconds=park_threshold,
            park_min_request_tokens=park_min_tokens,
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
        """
        Usable key indices, ordered from the rotation pointer.

        Excludes keys retired by a 401 (permanent) and keys parked by a
        long 429 whose window has not yet elapsed (temporary).
        """
        now = time.monotonic()
        n = len(self.api_keys)
        order = [(self._current_key_idx + i) % n for i in range(n)]
        live = []
        for i in order:
            if i in self._dead_key_idxs:
                continue
            parked_until = self._parked_until.get(i)
            if parked_until is not None:
                if now < parked_until:
                    continue
                # Window elapsed — bring the key back into rotation.
                del self._parked_until[i]
                logger.info(f"key {i + 1}/{n}: rate-limit window elapsed; back in rotation.")
            live.append(i)
        return live

    def _proportional_park_seconds(
        self, info: Dict[str, Any], wait: Optional[float]
    ) -> Optional[float]:
        """
        Scale a day-window park to the smallest request worth waking up for.

        Groq's TPD limit is a ROLLING window, not a daily bucket — established
        from two refusals on the same key 32m50s apart, where `Used` *fell* from
        99,588 to 98,426 while that key was only ever being refused. A bucket
        cannot decrease before it resets. A single decay rate (~0.59 tokens/s)
        also reproduced both quoted retry-after values to within 5%, so
        consumption ages out roughly linearly.

        `retry-after` therefore answers a narrow question: when will enough
        budget have decayed for *the request that was just refused*. Parking for
        that full window keeps the key out far longer than a smaller request
        needs. In the observed case it meant a 41.4-minute park when a
        500-token call would have fitted after 2.5 minutes.

        Given `limit`, `used` and `requested` from the refusal body:

            headroom        = limit - used            (available right now)
            deficit         = requested - headroom    (must decay for THAT call)
            target_deficit  = target - headroom       (must decay for a small one)
            park            = wait * target_deficit / deficit

        Returns `wait` unchanged when the figures are missing or the arithmetic
        does not apply, and 0.0 when a small request already fits. Waking a key
        slightly early costs one refused request; waking it 40 minutes late
        costs the whole run.
        """
        limit = info.get("limit")
        used = info.get("used")
        requested = info.get("requested")
        if wait is None or None in (limit, used, requested):
            return wait

        headroom = limit - used
        deficit = requested - headroom
        if deficit <= 0:
            return wait                      # refusal not explained by this call's size

        target_deficit = self._park_min_request - headroom
        if target_deficit <= 0:
            return 0.0                       # a small request fits already

        scaled = wait * (target_deficit / deficit)
        return max(0.0, min(scaled, wait))   # never longer than Groq's own answer

    def _park_key(self, idx: int, seconds: float, reason: str) -> None:
        """Take a key out of rotation until its rate-limit window elapses."""
        self._parked_until[idx] = time.monotonic() + seconds
        logger.warning(
            f"key {idx + 1}/{len(self.api_keys)}: parked for {seconds:.0f}s ({reason}). "
            f"{len(self._live_key_idxs())} key(s) still usable."
        )

    def _record_headroom(self, idx: int, headers: Any) -> None:
        """
        Log remaining rate-limit headroom from the `x-ratelimit-*` response
        headers, which Groq returns on every response, not just refusals.

        The header names are asymmetric and easy to misread:
            x-ratelimit-limit-requests -> requests per DAY    (RPD)
            x-ratelimit-limit-tokens   -> tokens  per MINUTE  (TPM)
        so they are labelled explicitly below rather than shown as a pair.

        Note there is a fourth limit, tokens per day (TPD), which appears in NO
        header. It is only visible in the body of a 429, so headroom against it
        cannot be reported here — see parse_rate_limit_error().
        """
        if headers is None:
            return
        try:
            get = headers.get
        except AttributeError:
            return

        snapshot = {
            "requests_remaining": get("x-ratelimit-remaining-requests"),
            "requests_limit": get("x-ratelimit-limit-requests"),
            "requests_reset": get("x-ratelimit-reset-requests"),
            "tokens_remaining": get("x-ratelimit-remaining-tokens"),
            "tokens_limit": get("x-ratelimit-limit-tokens"),
            "tokens_reset": get("x-ratelimit-reset-tokens"),
        }
        if not any(snapshot.values()):
            return
        self._headroom[idx] = snapshot

        logger.debug(
            f"key {idx + 1}/{len(self.api_keys)} headroom: "
            f"requests/day {snapshot['requests_remaining']}/{snapshot['requests_limit']} "
            f"(resets {snapshot['requests_reset']}), "
            f"tokens/min {snapshot['tokens_remaining']}/{snapshot['tokens_limit']} "
            f"(resets {snapshot['tokens_reset']}), "
            f"{self._daily_headroom_note(idx)}"
        )

        # Warn when a key is running low, so exhaustion is visible before it bites.
        for label, remaining, limit, reset in (
            ("requests/day", snapshot["requests_remaining"], snapshot["requests_limit"],
             snapshot["requests_reset"]),
            ("tokens/min", snapshot["tokens_remaining"], snapshot["tokens_limit"],
             snapshot["tokens_reset"]),
        ):
            try:
                rem, lim = int(remaining), int(limit)
            except (TypeError, ValueError):
                continue
            if lim and rem / lim < 0.10:
                logger.warning(
                    f"key {idx + 1}/{len(self.api_keys)}: {label} headroom low — "
                    f"{rem}/{lim} left, resets in {reset}"
                )

    def _daily_headroom_note(self, idx: int) -> str:
        """
        Human-readable daily-limit headroom for a key, derived from the last
        429 body rather than from a header.

        Groq publishes no `x-ratelimit-*` header for tokens-per-day, so this is
        an inference from the most recent refusal and is always labelled as
        such. A key that has never been refused reports "unknown" — no estimate
        is invented, and no request is ever sent merely to discover a limit.
        """
        seen = self._observed_daily.get(idx)
        if not seen:
            return "daily: unknown (no refusal seen yet)"

        remaining = seen["limit"] - seen["used"]
        age_s = time.monotonic() - seen["observed_at"]
        resets_at = seen.get("resets_at")

        if resets_at is not None and time.monotonic() >= resets_at:
            return (f"{seen['limit_type']}: window has since reset "
                    f"(was {remaining:,}/{seen['limit']:,} left); unknown until next refusal")

        reset_note = ""
        if resets_at is not None:
            reset_note = f", resets in {(resets_at - time.monotonic()) / 60:.0f}m"
        return (f"{seen['limit_type']}: ~{remaining:,}/{seen['limit']:,} left "
                f"[derived from refusal {age_s / 60:.0f}m ago, not live{reset_note}]")

    def headroom_report(self) -> Dict[int, Dict[str, Any]]:
        """Last observed rate-limit headroom per key index."""
        return dict(self._headroom)

    def daily_limit_report(self) -> Dict[int, Dict[str, Any]]:
        """
        Per-key daily-limit facts inferred from 429 bodies.

        Inference, not live measurement — see `_daily_headroom_note`.
        """
        return dict(self._observed_daily)

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
          - HTTP 413 → the key has less per-minute budget left than this request
            needs. Key-specific and time-varying, so rotate immediately to an
            untried key without backing off.
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

        # Budget: one sweep across every usable key, plus a few genuine retries
        # after backing off. The sweep costs no wall-clock time because untried
        # keys are tried immediately (see the 429 handler), so this is bounded
        # request count, not bounded delay.
        attempts = len(live) + self.max_total_retries
        rate_hits = 0
        transient_hits = 0

        # Keys already refused with a SHORT 429 during this call. Each key has
        # its own per-minute budget, so being throttled on one says nothing
        # about the next: sleeping before an untried key is pure dead time.
        # Sleep only once every usable key has actually been tried.
        throttled: set = set()
        pending_waits: List[float] = []

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

            except openai.RateLimitError as exc:
                rate_hits += 1
                info = parse_rate_limit_error(str(exc))
                limit_type = info.get("limit_type", "unknown")

                # Prefer the retry-after header; fall back to the wait quoted in
                # the message body. A 429 can be RPM, RPD, TPM or TPD, and those
                # need very different responses.
                wait = parse_duration_seconds(
                    getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
                    if getattr(exc, "response", None) is not None else None
                )
                if wait is None:
                    wait = info.get("retry_after_seconds")

                detail = (
                    f"{limit_type} {info.get('used', '?')}/{info.get('limit', '?')}"
                    if "limit" in info else limit_type
                )

                # A day-window refusal is the ONLY place TPD/RPD figures are
                # ever stated — they appear in no header. Cache them per key so
                # headroom can be reported (as an inference) afterwards.
                if info.get("window") == "day" and "limit" in info and "used" in info:
                    self._observed_daily[idx] = {
                        "limit_type": limit_type,
                        "limit": info["limit"],
                        "used": info["used"],
                        "requested": info.get("requested"),
                        "observed_at": time.monotonic(),
                        "resets_at": (time.monotonic() + wait) if wait is not None else None,
                    }

                if wait is not None and wait > self.park_threshold:
                    # Exhausted for this window, not briefly throttled. Retrying
                    # this key would burn attempts on a guaranteed refusal.
                    #
                    # For a day window the quoted retry-after answers only for
                    # the request that was refused. TPD decays continuously, so
                    # scale the park to the smallest request worth waking for.
                    park_for = wait
                    if info.get("window") == "day":
                        park_for = self._proportional_park_seconds(info, wait) or 0.0
                    reason = (
                        f"429 {detail}, retry in "
                        f"{info.get('retry_after_text', f'{wait:.0f}s')}"
                    )
                    if park_for < wait:
                        reason += (
                            f"; parking {park_for / 60:.1f}m instead — enough for a "
                            f"{self._park_min_request}-token call as the window decays"
                        )
                    self._park_key(idx, park_for, reason)
                    _rotate()
                    continue

                # Short 429: a brief per-minute throttle on THIS key only.
                delay = wait if wait is not None else self._backoff_seconds(attempt)
                throttled.add(idx)
                pending_waits.append(delay)
                _rotate()

                untried = [i for i in self._live_key_idxs() if i not in throttled]
                if untried:
                    # Another key has not been tried yet and carries its own
                    # per-minute budget. Sleeping here would be dead time: it
                    # was costing one full retry-after per key, so eight keys at
                    # 7.5s each burned ~60s before the first untried key was
                    # even attempted.
                    logger.warning(
                        f"{what}: rate limit on {key_label} ({detail}, hit #{rate_hits}); "
                        f"trying {len(untried)} untried key(s) before backing off "
                        f"[attempt {attempt + 1}/{attempts}]"
                    )
                    continue

                # Every usable key has now been tried and throttled. Only now is
                # waiting justified — and the shortest window is the first to
                # reopen, so wait for that rather than the longest.
                sleep_for = min(pending_waits) if pending_waits else delay
                logger.warning(
                    f"{what}: all {len(throttled)} usable key(s) rate-limited "
                    f"({detail}, hit #{rate_hits}); backing off {sleep_for:.1f}s "
                    f"[attempt {attempt + 1}/{attempts}]"
                )
                time.sleep(sleep_for)
                throttled.clear()
                pending_waits.clear()

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
                if exc.status_code == 413:
                    # "Request too large ... tokens per minute (TPM)". Read as a
                    # per-request size cap this looks fatal, so it used to fall
                    # through to the catch-all below: log once, return None, and
                    # every caller sees an indistinguishable empty result. On
                    # the summarisation path that meant a silent drop to
                    # extractive.
                    #
                    # It is not a size cap. It is a ROLLING per-minute
                    # consumption window, and the same key gives different
                    # verdicts minutes apart: one key here accepted a
                    # 34,072-token request and refused an 8,600-token one a few
                    # minutes later. So a refusal says something about that key
                    # right now, not about the request — which is exactly the
                    # situation rotation exists for, and 401 and 5xx were
                    # already using it.
                    #
                    # No backoff: an untried key has its own window and sleeping
                    # before it is dead time, the same reasoning the 429 handler
                    # applies. If every key refuses, the loop exhausts its
                    # attempts and returns None as before.
                    transient_hits += 1
                    logger.warning(
                        f"{what}: {key_label} refused this request size "
                        f"(413, per-minute budget); trying the next key "
                        f"[attempt {attempt + 1}/{attempts}]"
                    )
                    _rotate()
                    continue

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

    #: finish_reason from the most recent completion. Exposed for callers that
    #: want to tell "the model had nothing to say" from "we did not give it room
    #: to say it"; `chat()` itself returns None for both.
    _last_finish_reason: Optional[str] = None

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
                _USAGE.cache_hits += 1
                _USAGE_TOTAL.cache_hits += 1
                logger.debug("LLM cache hit — skipping API call")
                return cached

        def _do(client) -> Optional[str]:
            # with_raw_response exposes the x-ratelimit-* headers, which Groq
            # sends on every response and not just refusals. .parse() then
            # yields the same ChatCompletion the plain call would have returned.
            raw = client.chat.completions.with_raw_response.create(
                model=self.model,
                messages=messages,
                temperature=temp,
                max_tokens=max_tokens,
            )
            self._record_headroom(self._current_key_idx, getattr(raw, "headers", None))
            response = raw.parse()

            # Record usage before inspecting choices: the tokens were spent
            # whether or not the response turned out to be usable.
            usage = getattr(response, "usage", None)
            prompt_toks = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_toks = int(getattr(usage, "completion_tokens", 0) or 0)
            _USAGE.record_chat(self.model, prompt_toks, completion_toks)
            _USAGE_TOTAL.record_chat(self.model, prompt_toks, completion_toks)
            logger.debug(
                f"tokens: prompt={prompt_toks} completion={completion_toks} "
                f"model={self.model}"
            )

            if not response.choices:
                logger.warning("Groq API returned an empty choices array.")
                return None

            choice = response.choices[0]
            content = choice.message.content
            self._last_finish_reason = getattr(choice, "finish_reason", None)

            # Truncation is a DISTINCT failure and has to be loud.
            #
            # The return contract stays Optional[str] deliberately: of the eight
            # call sites, four treat a falsy reply as a hard error and three use
            # it as a legitimate fallback signal (structured_extraction drops to
            # regex, question_extraction returns no questions, the classifier
            # drops to heuristics). Raising here would turn three working
            # fallbacks into crashes. So the fix is to make the reason visible,
            # not to change what is returned.
            #
            # Why it matters: a reasoning model spends a variable prefix of the
            # budget thinking, so an under-sized max_tokens produces
            # finish_reason "length" with EMPTY content. That is not the model
            # failing, it is us asking for less room than it needs, and the two
            # were indistinguishable to every caller. An 80-token classifier
            # budget disabled LLM classification across a whole model migration
            # without a single warning.
            if self._last_finish_reason == "length":
                details = getattr(getattr(response, "usage", None),
                                  "completion_tokens_details", None)
                reasoning = getattr(details, "reasoning_tokens", None)
                detail = f", {reasoning} of them reasoning" if reasoning else ""
                if not content:
                    logger.warning(
                        f"LLM produced NO CONTENT: the {max_tokens}-token budget "
                        f"was consumed before the answer began{detail} "
                        f"(model={self.model}, finish_reason=length). This is a "
                        f"budget that is too small, not a model failure — raise "
                        f"max_tokens at the call site."
                    )
                else:
                    logger.warning(
                        f"LLM reply was TRUNCATED at the {max_tokens}-token "
                        f"budget{detail} (model={self.model}). The caller "
                        f"received a partial answer."
                    )

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
            _USAGE.transcriptions += 1
            _USAGE_TOTAL.transcriptions += 1
            text = response if isinstance(response, str) else getattr(response, "text", None)
            return text.strip() if isinstance(text, str) else None

        return self._run_with_rotation(_do, what="transcribe")

    def __repr__(self) -> str:
        return f"<LLMClient provider=groq model={self.model!r}>"
