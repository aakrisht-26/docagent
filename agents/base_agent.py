"""
BaseAgent — abstract interface for all DocAgent orchestrators.

Design principles:
    - Agents ORCHESTRATE, they do not implement logic.
    - All logic lives in skills; agents call skills in sequence.
    - Agents return exactly one typed object: PipelineResult.
    - Logging hooks are built-in so every agent gets traceability for free.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

from core.pipeline_result import PipelineResult
from utils.logger import get_logger


class BaseAgent(ABC):
    """
    Abstract base class for all DocAgent agents.

    Subclasses MUST:
        - Set `name` (str slug)
        - Set `description` (human-readable)
        - Implement `run(file_path: Path) -> PipelineResult`
    """

    name: str = ""
    description: str = ""

    #: Maps a step name (as passed to `_log_step`) to its canonical pipeline
    #: stage number, e.g. {"parse": "1", "structure_recognition": "3.5"}.
    #: Subclasses override this; an empty map just omits the stage prefix.
    stage_numbers: Dict[str, str] = {}

    #: Total number of numbered stages, used for the "n/N" prefix.
    total_stages: int = 6

    def __init__(self, config: Optional[Dict] = None) -> None:
        self.config: Dict = config or {}
        self.logger = get_logger(f"agent.{self.name or self.__class__.__name__}")
        self._pipeline_start: Optional[float] = None

    @abstractmethod
    def run(self, file_path: Path) -> PipelineResult:
        """
        Execute the full analysis pipeline on the given file.

        Args:
            file_path: Path to the document to process.

        Returns:
            PipelineResult with all analysis output and diagnostics.
        """
        ...

    # ── Logging helpers (available to all subclasses) ─────────────────

    def _begin_pipeline(self, label: str) -> None:
        """Mark the start of a run so stage logs can report cumulative elapsed time."""
        self._pipeline_start = time.monotonic()
        self.logger.info(f"═══ Pipeline START: {label} ═══")

    def _elapsed_ms(self) -> float:
        """Milliseconds since `_begin_pipeline`, or 0.0 if it was never called."""
        if self._pipeline_start is None:
            return 0.0
        return (time.monotonic() - self._pipeline_start) * 1000

    def _stage_prefix(self, skill_name: str) -> str:
        stage = self.stage_numbers.get(skill_name)
        return f"stage {stage}/{self.total_stages} " if stage else ""

    def _log_step(
        self,
        skill_name: str,
        success: bool,
        duration_ms: float,
        error: Optional[str] = None,
    ) -> None:
        # NOTE: the signature is load-bearing. ui/app.py wraps this method to
        # drive the progress bar and calls it positionally. Do not change the
        # parameters, and do not add new call sites for it — each call advances
        # the UI progress bar by one step. Use `_log_stage_skipped` instead for
        # stages that did not run.
        prefix = self._stage_prefix(skill_name)
        elapsed = self._elapsed_ms()
        if success:
            self.logger.info(
                f"  ✔ {prefix}[{skill_name}] completed in {duration_ms:.0f} ms "
                f"(elapsed {elapsed:.0f} ms)"
            )
        else:
            self.logger.error(
                f"  ✘ {prefix}[{skill_name}] FAILED in {duration_ms:.0f} ms "
                f"(elapsed {elapsed:.0f} ms) — {error}"
            )

    def _log_stage_skipped(self, skill_name: str, reason: str) -> None:
        """Log a stage the planner chose not to run.

        Deliberately NOT routed through `_log_step`: that would advance the UI
        progress bar for work that never happened.
        """
        prefix = self._stage_prefix(skill_name)
        self.logger.info(
            f"  – {prefix}[{skill_name}] skipped — {reason} "
            f"(elapsed {self._elapsed_ms():.0f} ms)"
        )

    def _log_pipeline_summary(self, label: str, timings: Dict[str, float],
                              total_ms: float) -> None:
        """Emit a per-stage timing breakdown at the end of a run."""
        self.logger.info(f"═══ Pipeline DONE: {label} — {total_ms:.0f} ms total ═══")
        if not timings:
            return
        widest = max(len(k) for k in timings)
        for step, ms in timings.items():
            share = (ms / total_ms * 100) if total_ms else 0.0
            prefix = self._stage_prefix(step) or "         "
            self.logger.info(
                f"    {prefix}{step:<{widest}}  {ms:8.0f} ms  {share:5.1f}%"
            )
        accounted = sum(timings.values())
        overhead = total_ms - accounted
        self.logger.info(
            f"    {'':9}{'overhead':<{widest}}  {overhead:8.0f} ms  "
            f"{(overhead / total_ms * 100) if total_ms else 0.0:5.1f}%"
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}'>"
