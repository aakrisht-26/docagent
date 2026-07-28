"""
BaseSkill — the abstract interface that every DocAgent skill must implement.

Design Contract
──────────────
- Skills are STATELESS: all inputs come via SkillInput, all outputs via SkillOutput.
- Skills never call other skills (that is the agent's responsibility).
- Skills are independently testable without an agent.
- Adding a new skill requires no changes to the registry, agent, or other skills.

Implementing a new skill:
    from skills.base_skill import BaseSkill, SkillInput, SkillOutput

    class MySkill(BaseSkill):
        name = "my_skill"
        description = "Does something useful."
        required_inputs = ["text"]

        def execute(self, inputs: SkillInput) -> SkillOutput:
            result = process(inputs.data["text"])
            return SkillOutput(success=True, data=result)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from core.models import SkillInput, SkillOutput
from utils.logger import get_logger


class BaseSkill(ABC):
    """
    Abstract base class for all DocAgent skills.

    Subclasses MUST:
        - Set `name`           : unique slug, e.g. "pdf_reader"
        - Set `description`    : human-readable one-liner
        - Set `required_inputs`: list of mandatory keys in SkillInput.data
        - Implement `execute(inputs) -> SkillOutput`
    """

    name: str = ""
    description: str = ""
    required_inputs: List[str] = []

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = config or {}
        self.logger = get_logger(f"skill.{self.name or self.__class__.__name__}")

    # ── Validation ────────────────────────────────────────────────────

    def validate_inputs(self, inputs: SkillInput) -> Optional[str]:
        """
        Validate that all required keys are present in inputs.data.

        Returns an error message string if validation fails, None if OK.
        """
        missing = [k for k in self.required_inputs if k not in inputs.data]
        if missing:
            return (
                f"Skill '{self.name}' missing required inputs: {missing}. "
                f"Received keys: {list(inputs.data.keys())}"
            )
        return None

    # ── Execution ─────────────────────────────────────────────────────

    @abstractmethod
    def execute(self, inputs: SkillInput) -> SkillOutput:
        """
        Core skill logic. Must be implemented by every concrete subclass.

        Implementations should:
        - Return SkillOutput(success=True, data=...) on success
        - Return SkillOutput(success=False, error="...") on known failures
        - Let unexpected exceptions bubble up to safe_execute()
        """
        ...

    def safe_execute(self, inputs: SkillInput) -> SkillOutput:
        """
        Protected wrapper around execute() that:
        1. Validates inputs (returns error SkillOutput if invalid)
        2. Times the execution
        3. Catches any unhandled exception and wraps it in a failed SkillOutput
        """
        error = self.validate_inputs(inputs)
        if error:
            self.logger.error(error)
            return SkillOutput(success=False, data=None, error=error)

        start = time.monotonic()
        try:
            result = self.execute(inputs)
            if result.duration_ms == 0.0:
                result.duration_ms = (time.monotonic() - start) * 1000
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error(
                f"Unhandled exception in skill '{self.name}': {exc}", exc_info=True
            )
            return SkillOutput(
                success=False,
                data=None,
                error=str(exc),
                duration_ms=duration_ms,
            )

    # ── Helpers ───────────────────────────────────────────────────────

    def get_config(self, key: str, default: Any = None) -> Any:
        """Convenience accessor for config values."""
        return self.config.get(key, default)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}'>"
