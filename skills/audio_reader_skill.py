"""
AudioReaderSkill — transcribes audio files and YouTube videos.

Engine:
    - YouTube: yt-dlp for download → pydub for format conversion → Groq Whisper for transcription
    - Local audio: pydub for format validation → Groq Whisper for transcription

Output:
    Returns ParsedDocument with file_type="audio" and full_text=transcript
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.models import DocumentChunk, ParsedDocument, SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.file_handler import extract_youtube_video_id, is_audio_file, make_temp_dir, cleanup_temp_dir
from utils.logger import get_logger

logger = get_logger(__name__)


class AudioReaderSkill(BaseSkill):
    """
    Transcribes audio files and YouTube videos into text.

    Supports input modes:
        1. file_path (str) — path to local audio file (.mp3, .m4a, .wav, .flac, .ogg, .webm)
        2. youtube_url (str) — YouTube video URL (extracted and transcribed)

    Config keys:
        groq (dict) — LLM client configuration (api_keys, model, timeout_seconds, temperature)
    """

    name = "audio_reader"
    description = "Transcribes audio files and YouTube videos using Groq Whisper API."
    required_inputs = []  # Either file_path OR youtube_url

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._groq_cfg = self.get_config("groq", {})
        self._max_duration_seconds = self.get_config("max_duration_seconds", 3600)
        self._temp_dir: Optional[Path] = None
        # Transcription model stays an explicit choice here rather than a
        # default buried in the shared client.
        self._transcription_model = self.get_config("transcription_model", "whisper-large-v3")

    # ── Skill entry point ─────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        """
        Main entry point. Routes to file-based or YouTube-based transcription.
        """
        data = inputs.data

        # Determine input mode
        youtube_url = data.get("youtube_url")
        file_path = data.get("file_path")

        if youtube_url:
            return self._process_youtube(youtube_url)
        elif file_path:
            return self._process_audio_file(Path(file_path))
        else:
            return SkillOutput(
                success=False,
                data=None,
                error="AudioReaderSkill requires either 'file_path' or 'youtube_url' in inputs",
            )

    # ── YouTube Processing ────────────────────────────────────────────

    def _process_youtube(self, youtube_url: str) -> SkillOutput:
        """Download and transcribe a YouTube video."""
        self._temp_dir = make_temp_dir()

        try:
            # Validate URL
            video_id = extract_youtube_video_id(youtube_url)
            if not video_id:
                return SkillOutput(
                    success=False,
                    data=None,
                    error=f"Invalid YouTube URL: {youtube_url}",
                )

            # Download audio
            audio_file = self._download_youtube_audio(youtube_url)
            if not audio_file:
                return SkillOutput(
                    success=False,
                    data=None,
                    error="Failed to download audio from YouTube",
                )

            # Transcribe
            transcript = self._transcribe_audio(audio_file, source="youtube")
            if transcript is None:
                return SkillOutput(
                    success=False,
                    data=None,
                    error="Failed to transcribe audio",
                )

            # Build ParsedDocument
            parsed_doc = ParsedDocument(
                file_name=f"youtube_{video_id}.audio",
                file_type="audio",
                chunks=[DocumentChunk(text=transcript, page_or_sheet=1, chunk_index=0)],
                full_text=transcript,
                tables=[],
                metadata={
                    "source": "youtube",
                    "video_id": video_id,
                    "url": youtube_url,
                    "transcription_model": "groq-whisper",
                },
                page_count=1,
            )

            return SkillOutput(success=True, data=parsed_doc)

        except Exception as exc:
            self.logger.error(f"YouTube processing failed: {exc}", exc_info=True)
            return SkillOutput(
                success=False,
                data=None,
                error=f"YouTube processing error: {str(exc)}",
            )
        finally:
            if self._temp_dir:
                cleanup_temp_dir(self._temp_dir)

    # ── Local Audio Processing ────────────────────────────────────────

    def _process_audio_file(self, file_path: Path) -> SkillOutput:
        """Transcribe a local audio file."""
        try:
            if not file_path.exists():
                return SkillOutput(
                    success=False,
                    data=None,
                    error=f"Audio file not found: {file_path}",
                )

            if not is_audio_file(file_path):
                return SkillOutput(
                    success=False,
                    data=None,
                    error=f"Not an audio file: {file_path}",
                )

            # Transcribe
            transcript = self._transcribe_audio(file_path, source="file")
            if transcript is None:
                return SkillOutput(
                    success=False,
                    data=None,
                    error="Failed to transcribe audio",
                )

            # Build ParsedDocument
            parsed_doc = ParsedDocument(
                file_name=file_path.name,
                file_type="audio",
                chunks=[DocumentChunk(text=transcript, page_or_sheet=1, chunk_index=0)],
                full_text=transcript,
                tables=[],
                metadata={
                    "source": "local_file",
                    "file_path": str(file_path),
                    "file_size_mb": file_path.stat().st_size / (1024 * 1024),
                    "transcription_model": "groq-whisper",
                },
                page_count=1,
            )

            return SkillOutput(success=True, data=parsed_doc)

        except Exception as exc:
            self.logger.error(f"Audio file processing failed: {exc}", exc_info=True)
            return SkillOutput(
                success=False,
                data=None,
                error=f"Audio processing error: {str(exc)}",
            )

    # ── YouTube Download ──────────────────────────────────────────────

    def _find_ffmpeg(self) -> Optional[str]:
        """
        Locate an ffmpeg binary for yt-dlp's audio conversion step.

        yt-dlp needs ffmpeg (an external program, NOT a pip package) to convert
        the downloaded stream to MP3. Search order:
          1. ffmpeg on the system PATH (system / conda / winget install)
          2. the binary bundled with the `imageio-ffmpeg` pip package
             (easiest install: `pip install imageio-ffmpeg` — no admin rights,
             no PATH changes needed)

        Returns a path for yt-dlp's `ffmpeg_location`, or None if ffmpeg could
        not be found anywhere. yt-dlp accepts either a directory (it then looks
        for binaries named `ffmpeg`/`ffprobe` inside) or a path to the binary
        itself. imageio-ffmpeg ships its binary under a versioned name
        (`ffmpeg-win-x86_64-v7.1.exe`), which the directory form cannot find —
        so that branch must return the full file path.
        """
        import shutil

        found = shutil.which("ffmpeg")
        if found:
            self.logger.debug(f"Using ffmpeg from PATH: {found}")
            return str(Path(found).parent)

        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and Path(exe).exists():
                self.logger.debug(f"Using ffmpeg bundled with imageio-ffmpeg: {exe}")
                return str(exe)
        except Exception:
            pass

        return None

    def _download_youtube_audio(self, youtube_url: str) -> Optional[Path]:
        """
        Download audio from YouTube using yt-dlp.

        Tries the Python module first; falls back to the yt-dlp CLI binary
        so this works even in cached Streamlit processes that loaded before
        the package was installed.

        Returns path to audio file on success, None on failure.
        """
        if not self._temp_dir:
            self._temp_dir = make_temp_dir()

        # ── Shared download options ───────────────────────────────────
        # Use mp3 (not wav) — WAV is uncompressed and will exceed Groq's 25 MB limit
        # for any video longer than ~5 minutes.
        output_template = str(self._temp_dir / "%(title)s")
        self.logger.info(f"Downloading audio from YouTube: {youtube_url}")

        # ── Locate ffmpeg before downloading ──────────────────────────
        # Without it, yt-dlp downloads the stream and then fails during
        # conversion with a long traceback. Fail early with a clear message.
        ffmpeg_dir = self._find_ffmpeg()
        if not ffmpeg_dir:
            self.logger.error(
                "ffmpeg not found — it is required to convert YouTube audio to MP3. "
                "Quick fix (no admin rights needed):  pip install imageio-ffmpeg\n"
                "Alternatives:  conda install -c conda-forge ffmpeg   |   "
                "winget install Gyan.FFmpeg\n"
                "Note: ffmpeg is an external program, so `pip install ffmpeg-python` "
                "alone is NOT enough. File uploads (PDF/Excel/CSV) are unaffected."
            )
            return None

        # ── Try Python module first ───────────────────────────────────
        try:
            import yt_dlp

            ydl_opts = {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "128",
                    }
                ],
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": ffmpeg_dir,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])

        except ImportError:
            # ── Fallback: CLI binary ──────────────────────────────────
            self.logger.warning("yt_dlp Python module not available; trying CLI binary")
            result = self._download_youtube_audio_cli(youtube_url, output_template)
            if not result:
                return None

        except Exception as exc:
            self.logger.error(f"YouTube download failed: {exc}", exc_info=True)
            return None

        # ── Find the downloaded MP3 file ──────────────────────────────
        audio_files = list(self._temp_dir.glob("*.mp3"))
        if not audio_files:
            self.logger.error("No audio file found after YouTube download")
            return None

        audio_file = audio_files[0]
        self.logger.info(f"Downloaded and converted to: {audio_file}")
        return audio_file

    def _download_youtube_audio_cli(self, youtube_url: str, output_template: str) -> bool:
        """
        Fallback: download via the yt-dlp CLI binary using subprocess.

        Returns True on success, False on failure.
        """
        import shutil

        ytdlp_bin = shutil.which("yt-dlp")
        if not ytdlp_bin:
            self.logger.error("yt-dlp CLI not found. Install with: pip install yt-dlp")
            return False

        cmd = [
            ytdlp_bin,
            "--format", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "128K",
            "--output", output_template,
            "--quiet",
            "--no-warnings",
        ]

        # Point the CLI at the same ffmpeg the module path uses, so a
        # pip-installed (imageio-ffmpeg) binary works here too.
        ffmpeg_dir = self._find_ffmpeg()
        if ffmpeg_dir:
            cmd += ["--ffmpeg-location", ffmpeg_dir]

        cmd.append(youtube_url)

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if completed.returncode != 0:
                self.logger.error(f"yt-dlp CLI failed: {completed.stderr.strip()}")
                return False
            return True
        except subprocess.TimeoutExpired:
            self.logger.error("yt-dlp CLI timed out after 300s")
            return False
        except Exception as exc:
            self.logger.error(f"yt-dlp CLI error: {exc}")
            return False

    # ── Transcription ─────────────────────────────────────────────────

    def _transcribe_audio(self, audio_file: Path, source: str = "file") -> Optional[str]:
        """
        Transcribe audio file using Groq's Whisper API.

        Routed through LLMClient so transcription shares the one implementation
        of timeout, retry/backoff and 401-aware key rotation with every other
        API call. This previously built its own `openai.OpenAI` client, used
        only the first key with no retry at all, and resolved keys itself —
        bypassing the placeholder filtering in resolve_groq_api_keys().

        Returns transcript on success, None on failure.
        """
        from utils.llm_client import LLMClient

        llm = LLMClient.from_config({"groq": self._groq_cfg})
        if not llm.available:
            self.logger.error(
                "No usable Groq API key configured. Set GROQ_API_KEYS or GROQ_API_KEY."
            )
            return None

        self.logger.info(f"Transcribing {source} audio: {audio_file.name}")

        transcript = llm.transcribe(audio_file, model=self._transcription_model)

        if not transcript:
            self.logger.warning("Transcription returned empty result")
            return None

        self.logger.info(f"Transcription complete: {len(transcript.split())} words")
        return transcript

    # ── Helpers ───────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}'>"
