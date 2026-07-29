"""Decisive check: after one key hits 429, is a DIFFERENT key also blocked?

Sends the smallest possible request (1 token) on several keys in turn and
reports, per key, whether it succeeds or is refused. If a limit is enforced at
the organisation level, every key is refused with the same limit type and
roughly the same retry window. If limits are per key, keys that have not been
driven to their limit still succeed.

Costs 1 token per key at most, and nothing at all for keys that are refused.

Usage:
    python tests/e2e/ratelimit_scope_check.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from utils.config import load_config, resolve_groq_api_keys  # noqa: E402


def parse_limit_error(message: str) -> dict:
    """Pull the structured facts out of a Groq 429 message."""
    out = {}
    org = re.search(r"in organization `([^`]+)`", message)
    if org:
        out["organization"] = org.group(1)
    kind = re.search(r"on (tokens per day \(TPD\)|tokens per minute \(TPM\)|"
                     r"requests per day \(RPD\)|requests per minute \(RPM\))", message)
    if kind:
        out["limit_type"] = kind.group(1)
    nums = re.search(r"Limit (\d+), Used (\d+), Requested (\d+)", message)
    if nums:
        out["limit"], out["used"], out["requested"] = (int(nums.group(i)) for i in (1, 2, 3))
    retry = re.search(r"try again in ([\dhms.]+)", message)
    if retry:
        # Strip the sentence-ending period the character class also matches.
        out["retry_after"] = retry.group(1).rstrip(".")
    return out


def main() -> int:
    import openai

    cfg = load_config()
    keys = resolve_groq_api_keys({"api_keys": cfg.groq.api_keys, "api_key": cfg.groq.api_key})

    # `--large` sends one sizeable request on the LAST key instead of a tiny one
    # on every key. A 1-token probe is not decisive when a daily token budget is
    # nearly — but not fully — exhausted: the small request still fits in the
    # remaining headroom and succeeds on every key, proving nothing. A request
    # bigger than the remaining headroom, sent on a key that has barely been
    # used, separates the two models cleanly:
    #   refused  -> the budget is shared organisation-wide
    #   accepted -> that key has its own budget
    if "--large" in sys.argv:
        idx = len(keys) - 1
        size = 1500
        prompt = "The quick brown fox jumps over the lazy dog. " * (size // 9)
        print(f"model={cfg.groq.model}\n")
        print(f"Sending a ~{size}-token request on key {idx + 1} "
              f"(the least-used key) while the daily token budget is nearly spent.\n")
        client = openai.OpenAI(api_key=keys[idx], base_url=cfg.groq.base_url, timeout=60.0)
        try:
            client.chat.completions.create(
                model=cfg.groq.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1, temperature=0,
            )
            print("  ACCEPTED.")
            print("  => This key has its OWN token budget. Limits are PER KEY,")
            print("     and rotating keys genuinely buys additional quota.")
        except openai.RateLimitError as exc:
            info = parse_limit_error(str(exc))
            print(f"  REFUSED (429): {info.get('limit_type', '?')}")
            print(f"    organisation : {info.get('organization', '?')}")
            print(f"    used/limit   : {info.get('used', '?')}/{info.get('limit', '?')}")
            print(f"    retry after  : {info.get('retry_after', '?')}")
            print()
            print("  => A barely-used key was refused because of consumption on a")
            print("     DIFFERENT key. The budget is shared ORGANISATION-WIDE, so")
            print("     rotating keys buys nothing for this limit.")
        return 0

    print(f"model={cfg.groq.model}  keys={len(keys)}\n")
    print(f"{'key':>4}  {'result':<9} detail")
    print("-" * 78)

    orgs, kinds, blocked, ok = set(), set(), 0, 0

    for i, key in enumerate(keys):
        client = openai.OpenAI(api_key=key, base_url=cfg.groq.base_url, timeout=60.0)
        try:
            client.chat.completions.create(
                model=cfg.groq.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                temperature=0,
            )
            ok += 1
            print(f"{i + 1:>4}  {'OK':<9} request accepted")
        except openai.RateLimitError as exc:
            blocked += 1
            info = parse_limit_error(str(exc))
            orgs.add(info.get("organization", "?"))
            kinds.add(info.get("limit_type", "?"))
            print(f"{i + 1:>4}  {'429':<9} {info.get('limit_type', '?')} | "
                  f"used {info.get('used', '?')}/{info.get('limit', '?')} | "
                  f"retry in {info.get('retry_after', '?')} | org {info.get('organization', '?')}")
        except Exception as exc:
            print(f"{i + 1:>4}  {'ERROR':<9} {type(exc).__name__}: {str(exc)[:90]}")

    print("-" * 78)
    print(f"  accepted: {ok}   refused: {blocked}")
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    if blocked and not ok:
        print("  ORGANISATION-LEVEL. Every key was refused by the same limit.")
        print(f"    limit type(s): {sorted(kinds)}")
        print(f"    organisation(s): {sorted(orgs)}")
        print("    => All keys draw on one pool. Rotating keys buys nothing for")
        print("       this limit; the only options are to wait or to reduce usage.")
    elif blocked and ok:
        print("  MIXED / PER-KEY for this limit. Some keys were refused while")
        print(f"  others still worked ({ok} accepted, {blocked} refused).")
        print("    => Rotation genuinely helps here.")
    elif ok and not blocked:
        print("  No limit currently active — every key was accepted.")
        print("    Re-run while a limit is being hit to classify its scope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
