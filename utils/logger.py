"""
Centralized logging for DocAgent.

Features:
- Singleton-style initialization (call setup_logging() once at startup)
- Rich console output with color / tracebacks when `rich` is installed
- File handler for persistent DEBUG logs in logs/docagent.log
- `get_logger(name)` returns a scoped logger under the "docagent" namespace

Usage:
    from utils.logger import get_logger, setup_logging

    setup_logging(level="INFO", log_file="logs/docagent.log")
    logger = get_logger(__name__)
    logger.info("Ready.")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_INITIALIZED: bool = False


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    use_rich: bool = True,
) -> None:
    """
    Configure the root 'docagent' logger.

    Should be called exactly once at application startup (ui/app.py or CLI entry).
    Subsequent calls are silently ignored.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger("docagent")
    root.setLevel(logging.DEBUG)  # root captures everything; handlers filter
    root.propagate = False        # don't bubble up to Python root logger

    # ── Console handler ───────────────────────────────────────────────
    if use_rich:
        try:
            from rich.logging import RichHandler

            console = RichHandler(
                level=log_level,
                rich_tracebacks=True,
                markup=True,
                show_path=False,
                log_time_format="[%H:%M:%S]",
            )
            root.addHandler(console)
        except ImportError:
            _add_stream_handler(root, log_level)
    else:
        _add_stream_handler(root, log_level)

    # ── File handler (DEBUG level always) ────────────────────────────
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(name)-40s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(fh)

    _INITIALIZED = True


def _add_stream_handler(logger: logging.Logger, level: int) -> None:
    """Fallback plain-text stream handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(name)-40s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """
    Return a scoped logger under the 'docagent' namespace.

    Examples:
        get_logger(__name__)            → docagent.skills.pdf_reader_skill
        get_logger("agent.document")    → docagent.agent.document
    """
    if name.startswith("docagent"):
        return logging.getLogger(name)
    return logging.getLogger(f"docagent.{name}")
