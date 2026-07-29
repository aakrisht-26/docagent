"""Diagnostic: are Groq rate limits enforced per API key, or per organisation?

This decides whether the multi-key rotation in LLMClient is worth anything. If
all 8 keys belong to one organisation they share a single quota pool, and
rotating on 429 just moves the same exhausted budget between keys.

Method
------
Groq returns `x-ratelimit-*` headers on EVERY response, not only on 429s, so the
question can be answered by reading counters rather than by exhausting a key.

That matters: deliberately driving a key to 429 would burn a real chunk of a
free-tier daily quota, take a long time, and still leave the shared-vs-separate
question ambiguous (a 429 on key B right after key A is suggestive, not proof —
B might have been independently low).

Reading the counters is decisive instead. The probe interleaves requests
A -> B -> A and watches `x-ratelimit-remaining-requests`:

  * SHARED (organisation-level): the counter keeps descending across keys.
    A=99, B=98, A=97 — key B continues key A's countdown.
  * SEPARATE (per-key): each key descends on its own track.
    A=99, B=4999, A=98 — key B is unrelated to key A.

Header naming is asymmetric and easy to misread:
  x-ratelimit-limit-requests  -> requests per DAY   (RPD)
  x-ratelimit-limit-tokens    -> tokens  per MINUTE (TPM)
The reset fields follow their own metric, so they are reported separately below
rather than being assumed to share a window.

Cost: one minimal request per key, plus two extra for the interleave. Each is a
1-token completion.

Usage:
    python tests/e2e/ratelimit_probe.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from utils.config import load_config, resolve_groq_api_keys  # noqa: E402

RATELIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "retry-after",
)


def probe(client, model: str, prompt: str = "hi", max_tokens: int = 1) -> dict:
    """One request; return the rate-limit headers from the raw response."""
    raw = client.chat.completions.with_raw_response.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0,
    )
    headers = raw.headers
    return {h: headers.get(h) for h in RATELIMIT_HEADERS}


def big_prompt(approx_tokens: int = 2000) -> str:
    """Filler roughly `approx_tokens` long (~4 chars per token)."""
    return ("The quick brown fox jumps over the lazy dog. " * (approx_tokens // 9 + 1))


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    import openai

    cfg = load_config()
    keys = resolve_groq_api_keys({"api_keys": cfg.groq.api_keys, "api_key": cfg.groq.api_key})
    if len(keys) < 2:
        print(f"Need at least 2 keys to answer this; found {len(keys)}.")
        return 2

    model = cfg.groq.model
    base_url = cfg.groq.base_url
    print(f"model={model}  keys={len(keys)}\n")

    def client_for(i):
        return openai.OpenAI(api_key=keys[i], base_url=base_url, timeout=60.0)

    # ── Pass 1: one request per key ───────────────────────────────────────────
    print("=" * 78)
    print("PASS 1 — one request per key")
    print("=" * 78)
    print(f"{'key':>4}  {'req remaining':>14} {'req limit':>10}  "
          f"{'tok remaining':>14} {'tok limit':>10}  {'reset req':>10} {'reset tok':>10}")

    first = {}
    for i in range(len(keys)):
        try:
            h = probe(client_for(i), model)
        except Exception as exc:
            print(f"{i + 1:>4}  ERROR: {type(exc).__name__}: {exc}")
            continue
        first[i] = h
        print(f"{i + 1:>4}  {str(h['x-ratelimit-remaining-requests']):>14} "
              f"{str(h['x-ratelimit-limit-requests']):>10}  "
              f"{str(h['x-ratelimit-remaining-tokens']):>14} "
              f"{str(h['x-ratelimit-limit-tokens']):>10}  "
              f"{str(h['x-ratelimit-reset-requests']):>10} "
              f"{str(h['x-ratelimit-reset-tokens']):>10}")

    if len(first) < 2:
        print("\nToo few keys responded to draw a conclusion.")
        return 1

    # ── Pass 2: interleave A -> B -> A ────────────────────────────────────────
    idx = sorted(first)[:2]
    a, b = idx[0], idx[1]
    print()
    print("=" * 78)
    print(f"PASS 2 — interleave key {a + 1} -> key {b + 1} -> key {a + 1}")
    print("=" * 78)

    seq = []
    for label, i in ((f"key{a + 1}", a), (f"key{b + 1}", b), (f"key{a + 1}", a)):
        h = probe(client_for(i), model)
        rem = as_int(h["x-ratelimit-remaining-requests"])
        seq.append((label, rem))
        print(f"  {label:>7}  remaining-requests = {rem}")

    # ── Pass 3: is the TOKEN (TPM) pool shared? ───────────────────────────────
    # Pass 2 only settles requests-per-day. The token counters read identically
    # across keys, which on its own cannot distinguish "per-key and coincidentally
    # equal" from "one shared pool" — every probe so far consumed almost nothing.
    # So: spend a visible number of tokens on key A, then immediately re-read
    # key B. If B's remaining drops too, the pool is shared.
    print()
    print("=" * 78)
    print(f"PASS 3 — token pool: spend ~2000 tokens on key {a + 1}, watch key {b + 1}")
    print("=" * 78)

    b_before = as_int(probe(client_for(b), model)["x-ratelimit-remaining-tokens"])
    print(f"  key{b + 1} remaining-tokens BEFORE      = {b_before}")

    a_big = probe(client_for(a), model, prompt=big_prompt(2000), max_tokens=1)
    a_after = as_int(a_big["x-ratelimit-remaining-tokens"])
    print(f"  key{a + 1} remaining-tokens after spend = {a_after}  (spent on key {a + 1})")

    b_after = as_int(probe(client_for(b), model)["x-ratelimit-remaining-tokens"])
    print(f"  key{b + 1} remaining-tokens AFTER       = {b_after}")

    token_pool_shared = None
    if None not in (b_before, b_after):
        drop = b_before - b_after
        print(f"  key{b + 1} dropped by {drop} tokens while the spend happened on key {a + 1}")
        # A shared pool would show key B absorbing most of key A's ~2000 spend.
        token_pool_shared = drop > 1000

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)

    (_, r1), (_, r2), (_, r3) = seq
    if None in (r1, r2, r3):
        print("  INCONCLUSIVE — remaining-requests header absent or unparseable.")
        return 1

    limits = {as_int(h["x-ratelimit-limit-requests"]) for h in first.values()}
    same_limit = len(limits) == 1

    # Shared pool: the counter keeps descending as it moves between keys.
    descending_across_keys = (r2 == r1 - 1) and (r3 == r2 - 1)
    # Separate pools: key A's own track continues while B sits elsewhere.
    independent_tracks = (r3 == r1 - 1) and abs(r2 - r1) > 1

    if descending_across_keys:
        print("  SHARED — rate limits are enforced at the ORGANISATION level.")
        print(f"    The counter descended {r1} -> {r2} -> {r3} as requests moved")
        print("    between two different keys, so both draw on one pool.")
        print("    => Rotating keys on 429 does NOT buy extra quota.")
    elif independent_tracks:
        print("  SEPARATE — each key has its OWN quota.")
        print(f"    Key A tracked {r1} -> {r3} while key B sat at {r2}.")
        print("    => Rotating keys on 429 genuinely buys extra quota.")
    else:
        print("  INCONCLUSIVE — the pattern matches neither model cleanly.")
        print(f"    observed: {r1} -> {r2} -> {r3}")
        print("    Re-run when idle; concurrent traffic on these keys will skew it.")

    print()
    if token_pool_shared is True:
        print("  TOKENS (TPM): SHARED across keys — spending on one key drained the other.")
        print("    => Rotation does NOT help once the per-minute token budget is gone.")
    elif token_pool_shared is False:
        print("  TOKENS (TPM): SEPARATE per key — spending on one key left the other intact.")
        print("    => Rotation genuinely buys additional token headroom.")
    else:
        print("  TOKENS (TPM): INCONCLUSIVE — header missing or unparseable.")

    print()
    print(f"  All keys report the same requests-per-day limit: {same_limit} "
          f"({sorted(x for x in limits if x is not None)})")
    print()
    print("  Header semantics (asymmetric — do not assume a shared window):")
    sample = first[a]
    print(f"    x-ratelimit-limit-requests = {sample['x-ratelimit-limit-requests']}"
          f"  -> requests per DAY,    resets in {sample['x-ratelimit-reset-requests']}")
    print(f"    x-ratelimit-limit-tokens   = {sample['x-ratelimit-limit-tokens']}"
          f"  -> tokens  per MINUTE,  resets in {sample['x-ratelimit-reset-tokens']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
