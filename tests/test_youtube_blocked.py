"""Tests for telling an external YouTube block from a real failure.

The `youtube` e2e stage failed with YouTube's "Sign in to confirm you're not a
bot" interstitial, minutes after the same video downloaded fine. Nothing in the
repo was wrong: shared and flagged egress addresses get that response, and a
retry from elsewhere succeeds.

That leaves the harness with a stage that can go red for reasons unrelated to
the code, which makes a red run ambiguous — and an ambiguous red is dangerous
in a specific way. After a few runs where red meant "the bot check again", a
genuine regression gets waved through with the same shrug. That is the same
disease as a false green, arriving from the other side.

So a blocked run is neither PASS nor FAIL. It exits 3, prints BLOCKED, and says
in words that nothing was verified.

THE RISK IN THIS DESIGN IS MASKING, AND IT IS WHAT MOST OF THIS FILE TESTS.
A detector that shrugs off failures it does not recognise would convert every
real YouTube bug into a skipped stage. Three rules keep it narrow, and each has
tests here:

  1. Only a failure AT THE DOWNLOAD STEP can be blocked. Anything that fails
     after the audio is in hand is our code, whatever the message says.
  2. Only allowlisted signatures count. Unrecognised text is UNKNOWN, which is
     a failure.
  3. A removed, private or age-gated video is NOT a block. It is outside the
     repo, but it means the fixture URL needs replacing — work for this
     project, and it must stay red.

Run:
    pytest tests/test_youtube_blocked.py -v
"""

from __future__ import annotations

import unittest

from utils.youtube_errors import (
    BLOCKED, SETUP, UNAVAILABLE, UNKNOWN, classify_download_error, explain,
    is_external_block,
)

#: The text actually reported, as yt-dlp relays it.
BOT_CHECK = ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a "
             "bot. This helps protect our community. Learn more")


class TestClassification(unittest.TestCase):
    def test_the_reported_bot_check_is_a_block(self):
        self.assertEqual(classify_download_error(BOT_CHECK), BLOCKED)

    def test_rate_limiting_is_a_block(self):
        for text in ("ERROR: Unable to download webpage: HTTP Error 429: Too "
                     "Many Requests",
                     "giving up after 3 retries: rate limit exceeded"):
            with self.subTest(text=text[:40]):
                self.assertEqual(classify_download_error(text), BLOCKED)

    def test_an_age_gate_is_not_a_block(self):
        """One word from the bot check and the opposite meaning.

        "Sign in to confirm your age" is a permanent property of the video;
        "Sign in to confirm you're not a bot" is a transient property of this
        IP. Retrying clears one and never clears the other, so they cannot
        share a verdict.
        """
        self.assertEqual(
            classify_download_error("ERROR: [youtube] x: Sign in to confirm "
                                    "your age"), UNAVAILABLE)

    def test_a_missing_video_is_not_a_block(self):
        for text in ("ERROR: [youtube] x: Video unavailable",
                     "ERROR: [youtube] x: Private video. Sign in if you have "
                     "been granted access",
                     "This video has been removed by the uploader"):
            with self.subTest(text=text[:40]):
                self.assertEqual(classify_download_error(text), UNAVAILABLE)

    def test_a_geo_restriction_is_not_a_block(self):
        """Also external, also not a bot check. Retrying from another IP might
        fix it, but it is the video's availability, not a refusal to serve
        this client, so it does not get the transient verdict."""
        self.assertEqual(
            classify_download_error("The uploader has not made this video "
                                    "available in your country"), UNAVAILABLE)

    def test_a_missing_program_is_setup(self):
        self.assertEqual(
            classify_download_error("ffprobe and ffmpeg not found. Please "
                                    "install or provide the path"), SETUP)

    def test_anything_unrecognised_is_unknown(self):
        """The default, and the reason the whole design is safe."""
        for text in ("KeyError: 'streamingData'",
                     "AttributeError: 'NoneType' object has no attribute 'get'",
                     "TypeError: expected str, got dict",
                     "some entirely novel yt-dlp failure"):
            with self.subTest(text=text[:40]):
                self.assertEqual(classify_download_error(text), UNKNOWN)

    def test_it_never_raises_on_junk(self):
        """It runs inside an error path. Raising there would replace a
        diagnosable failure with a confusing one."""
        for text in (None, "", "   ", 0, 3.14, [], {}, object()):
            with self.subTest(text=repr(text)[:30]):
                self.assertIn(classify_download_error(text),
                              (BLOCKED, UNAVAILABLE, SETUP, UNKNOWN))

    def test_case_and_surrounding_noise_do_not_matter(self):
        self.assertEqual(
            classify_download_error("WARNING: ... SIGN IN TO CONFIRM YOU'RE "
                                    "NOT A BOT ... traceback follows"), BLOCKED)

    def test_is_external_block_is_narrower_than_failure(self):
        self.assertTrue(is_external_block(BOT_CHECK))
        for text in ("ERROR: Video unavailable", "KeyError: 'streamingData'",
                     "ffmpeg not found", None):
            with self.subTest(text=str(text)[:30]):
                self.assertFalse(is_external_block(text))

    def test_every_reason_explains_itself(self):
        for reason in (BLOCKED, UNAVAILABLE, SETUP, UNKNOWN):
            with self.subTest(reason=reason):
                self.assertGreater(len(explain(reason)), 40)

    def test_the_block_explanation_says_it_is_not_our_fault(self):
        """The user reading it needs to know retrying elsewhere is the fix, and
        that nothing is broken here."""
        text = explain(BLOCKED).lower()
        self.assertIn("network", text)
        self.assertNotIn("invalid", text)


class TestTheHarnessDetectorCannotMaskABug(unittest.TestCase):
    """The rules that keep BLOCKED from swallowing a real failure."""

    def _result(self, errors, success=False):
        from core.pipeline_result import PipelineResult
        return PipelineResult(
            file_name="youtube_x.audio", file_type="youtube",
            doc_type="unknown", domain="General",
            classification_confidence=0.0, classification_method="none",
            summary="", summary_method="none",
            questions=[], question_extraction_method="none",
            raw_text="", word_count=0, page_count=0, metadata={},
            errors=errors, success=success,
        )

    def _blocked(self, result) -> bool:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "tests" / "e2e"))
        import e2e
        return bool(e2e._blocked_reason(result))

    def test_a_download_bot_check_is_blocked(self):
        self.assertTrue(self._blocked(self._result(
            [f"YouTube audio extraction failed: Failed to download audio from "
             f"YouTube [blocked]: ... (underlying error: {BOT_CHECK})"])))

    def test_a_failure_after_a_successful_download_is_never_blocked(self):
        """RULE 1, and the most important test in this file.

        The bot-check text is deliberately present in the message. If the
        download succeeded, the failure is ours, and no amount of matching
        text may excuse it — otherwise a transcription or summarisation
        regression could hide behind a quoted error string.
        """
        self.assertFalse(self._blocked(self._result(
            [f"Transcription failed after download. Log context: {BOT_CHECK}"])))

    def test_an_unrecognised_download_failure_is_not_blocked(self):
        """RULE 2. This is the shape a genuine yt-dlp integration bug takes."""
        self.assertFalse(self._blocked(self._result(
            ["YouTube audio extraction failed: Failed to download audio from "
             "YouTube [unknown]: ... (underlying error: KeyError 'streamingData')"])))

    def test_a_dead_fixture_url_is_not_blocked(self):
        """RULE 3. Outside the repo, but it is still work for this repo."""
        self.assertFalse(self._blocked(self._result(
            ["YouTube audio extraction failed: Failed to download audio from "
             "YouTube [unavailable]: ... (underlying error: Video unavailable)"])))

    def test_a_successful_run_is_never_blocked(self):
        self.assertFalse(self._blocked(self._result([], success=True)))

    def test_no_errors_at_all_is_not_blocked(self):
        self.assertFalse(self._blocked(self._result([])))
        self.assertFalse(self._blocked(self._result(None)))


class TestTheReasonSurvivesToTheCaller(unittest.TestCase):
    """It used to be logged and discarded, in two places.

    The skill replaced yt-dlp's text with "Failed to download audio from
    YouTube", and run_youtube then replaced that with "YouTube audio extraction
    failed". A bot check, a deleted video and a real bug all reached the user
    as the same sentence.
    """

    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def _run_with_download_error(self, message: str):
        """Inject the failure at the yt-dlp boundary, so every layer under
        test actually runs: capture, classification, skill message, agent
        propagation."""
        import yt_dlp
        from agents.document_agent import DocumentAgent
        from utils.config import load_config

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def download(self, urls): raise Exception(message)

        real = yt_dlp.YoutubeDL
        yt_dlp.YoutubeDL = FakeYDL
        try:
            return DocumentAgent(config=load_config().to_dict()).run_youtube(self.URL)
        finally:
            yt_dlp.YoutubeDL = real

    def test_the_bot_check_reaches_the_pipeline_result(self):
        result = self._run_with_download_error(BOT_CHECK)
        self.assertFalse(result.success)
        joined = " ".join(result.errors).lower()
        self.assertIn("not a bot", joined,
                      "the reason must survive; it used to be logged and lost")
        self.assertIn("blocked", joined)

    def test_the_message_tells_the_user_what_to_do(self):
        result = self._run_with_download_error(BOT_CHECK)
        self.assertIn("network", " ".join(result.errors).lower())

    def test_a_different_cause_produces_a_different_message(self):
        """The point of propagating it: these used to be identical strings."""
        blocked = " ".join(self._run_with_download_error(BOT_CHECK).errors)
        gone = " ".join(self._run_with_download_error(
            "ERROR: [youtube] x: Video unavailable").errors)
        self.assertNotEqual(blocked, gone)
        self.assertIn("unavailable", gone.lower())

    def test_the_reason_does_not_leak_between_attempts(self):
        """A stale reason reported against a later failure would be worse than
        no reason at all."""
        self._run_with_download_error(BOT_CHECK)
        second = self._run_with_download_error("KeyError: 'streamingData'")
        self.assertNotIn("not a bot", " ".join(second.errors).lower())


class TestExitCodes(unittest.TestCase):
    """BLOCKED must be distinguishable from both PASS and FAIL by a machine."""

    def _e2e(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "e2e"))
        import e2e
        return e2e

    def test_the_four_codes_are_distinct(self):
        e2e = self._e2e()
        codes = [e2e.EXIT_PASS, e2e.EXIT_FAIL, e2e.EXIT_USAGE, e2e.EXIT_BLOCKED]
        self.assertEqual(len(set(codes)), 4)

    def test_blocked_is_not_success(self):
        """The whole point. A blocked run did not verify the YouTube path, and
        exiting 0 would claim it did."""
        e2e = self._e2e()
        self.assertNotEqual(e2e.EXIT_BLOCKED, e2e.EXIT_PASS)

    def test_blocked_is_not_the_failure_code(self):
        e2e = self._e2e()
        self.assertNotEqual(e2e.EXIT_BLOCKED, e2e.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
