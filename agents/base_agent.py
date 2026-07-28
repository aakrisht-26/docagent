"""
BaseAgent — abstract interface for all DocAgent orchestrators.

Design principles:
    - Agents ORCHESTRATE, they do not implement logic.
    - All logic lives in skills; agents call skills in sequence.
    - Agents return exactly one typed object: PipelineResult.
    - Logging hooks are built-in so every agent gets traceability for free.
"""

from __future__ import annotations

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

    def __init__(self, config: Optional[Dict] = None) -> None:
        self.config: Dict = config or {}
        self.logger = get_logger(f"agent.{self.name or self.__class__.__name__}")

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

    def _log_step(
        self,
        skill_name: str,
        success: bool,
        duration_ms: float,
        error: Optional[str] = None,
    ) -> None:
        if success:
            self.logger.info(
                f"  ✔ [{skill_name}] completed in {duration_ms:.0f} ms"
            )
        else:
            self.logger.error(
                f"  ✘ [{skill_name}] FAILED in {duration_ms:.0f} ms — {error}"
            )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}'>"
