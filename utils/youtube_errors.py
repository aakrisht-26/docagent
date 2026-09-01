"""Why a YouTube download failed, in a form a caller can act on.

`yt-dlp` reports failures as prose relayed from YouTube's servers. That prose is
the only signal distinguishing four situations that need four different
responses:

  BLOCKED      YouTube declined to serve THIS machine right now — the
               "Sign in to confirm you're not a bot" interstitial, or a 429.
               Nothing is wrong with the code, the URL or the video. The same
               request from another IP succeeds, often minutes apart. Shared
               egress IPs (Streamlit Community Cloud, CI runners, VPNs, campus
               NAT) hit it far more often than a home connection.
  UNAVAILABLE  The video itself cannot be fetched — removed, private, age-gated
               or geo-restricted. Retrying changes nothing; the URL is the
               problem, which for a test fixture means the fixture needs
               replacing.
  SETUP        This machine is missing something — ffmpeg, the yt-dlp binary.
               Actionable locally, and covered by README step 3.
  UNKNOWN      Anything else. Deliberately the DEFAULT.

WHY UNKNOWN DEFAULTS TO "TREAT AS A REAL FAILURE". This module exists so the
e2e harness can tell an external block from a regression in our own code. A
classifier that guessed "probably external" on unrecognised text would convert
every genuine YouTube-path bug into a skipped stage, which is precisely the
failure this project keeps finding: a check that reports success because it
never really looked. Matching is therefore an allowlist of signatures observed
or documented, and everything else is a failure.

The patterns are matched against text YouTube controls, so they will drift.
That is survivable in this direction only: a missed BLOCKED signature shows up
as a confusing red run, which a human then investigates. A false BLOCKED would
hide a bug silently. The asymmetry is the whole design.
"""

from __future__ import annotations

BLOCKED = "blocked"
UNAVAILABLE = "unavailable"
SETUP = "setup"
UNKNOWN = "unknown"

#: Substrings (lowercased) that mean "YouTube refused THIS client, right now".
#: Kept deliberately narrow — each is a bot-check or rate-limit signature, not a
#: general "download went wrong". `confirm you're not a bot` is the interstitial
#: reported from a home IP minutes after the same video downloaded fine.
_BLOCKED_SIGNATURES = (
    "not a bot",                      # "Sign in to confirm you're not a bot"
    "this helps protect our community",
    "http error 429",
    "too many requests",
    "rate limit",
    "sign in to confirm youre not a bot",
)

#: The video, not the environment. Retrying is pointless; the URL must change.
#: `sign in to confirm your age` lives HERE, not in BLOCKED, even though it also
#: begins "Sign in to confirm" — an age gate is a property of the video and no
#: amount of retrying or changing IP clears it.
_UNAVAILABLE_SIGNATURES = (
    "video unavailable",
    "video is unavailable",
    "private video",
    "this video is private",
    "has been removed",
    "account associated with this video has been terminated",
    "confirm your age",
    "age-restricted",
    "not available in your country",
    "not made this video available",
    "blocked it on copyright grounds",
)

#: This machine is missing a program. Nothing to do with YouTube.
_SETUP_SIGNATURES = (
    "ffmpeg not found",
    "ffprobe and ffmpeg not found",
    "ffprobe not found",
    "postprocessing:",
    "yt-dlp cli not found",
)


def classify_download_error(text: object) -> str:
    """Classify a yt-dlp failure. Returns BLOCKED / UNAVAILABLE / SETUP / UNKNOWN.

    `text` is whatever the caller has — an exception, a stderr dump, None. It is
    coerced rather than validated, because the one thing this must not do is
    raise inside an error path.

    UNAVAILABLE and SETUP are checked BEFORE blocked. "Sign in to confirm your
    age" and "Sign in to confirm you're not a bot" are one word apart and mean
    opposite things: one is a permanent property of the video, the other is a
    transient property of this IP.
    """
    if text is None:
        return UNKNOWN
    haystack = str(text).lower()
    if not haystack.strip():
        return UNKNOWN

    for signature in _SETUP_SIGNATURES:
        if signature in haystack:
            return SETUP
    for signature in _UNAVAILABLE_SIGNATURES:
        if signature in haystack:
            return UNAVAILABLE
    for signature in _BLOCKED_SIGNATURES:
        if signature in haystack:
            return BLOCKED
    return UNKNOWN


def is_external_block(text: object) -> bool:
    """True only for a transient refusal by YouTube of this client.

    The narrow question the e2e harness asks. Deliberately not "did the
    download fail for a reason outside the repo" — a removed video is also
    outside the repo, but it means a fixture needs replacing, which IS work for
    this project and must stay a red stage.
    """
    return classify_download_error(text) == BLOCKED


def explain(reason: str) -> str:
    """A sentence a human can act on, for the UI and the harness."""
    return {
        BLOCKED: (
            "YouTube declined to serve this request, asking the downloader to "
            "confirm it is not a bot. This is a limit on the network address "
            "you are calling from, not a problem with the video or with "
            "DocAgent. Shared addresses — cloud hosting, CI, VPNs, office or "
            "campus networks — are flagged far more often than home "
            "connections. Trying again later, or from a different network, "
            "usually works."
        ),
        UNAVAILABLE: (
            "The video itself could not be fetched — it may be private, "
            "removed, age-restricted, or unavailable in this region. Retrying "
            "will not help; check the URL."
        ),
        SETUP: (
            "A required program is missing on this machine. ffmpeg is needed "
            "to convert the downloaded audio: `pip install imageio-ffmpeg` is "
            "the quickest fix and needs no admin rights. See README step 3."
        ),
        UNKNOWN: (
            "The download failed for an unrecognised reason. The underlying "
            "error is included above and in the logs."
        ),
    }.get(reason, "The download failed.")
