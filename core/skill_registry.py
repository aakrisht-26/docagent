"""
SkillRegistry — singleton for dynamic skill auto-discovery and registration.

How it works:
1. On `discover()`, scans every module in the `skills/` package.
2. For each module, finds all non-abstract BaseSkill subclasses.
3. Registers them by their `name` attribute.

Adding a new skill:
    1. Create `skills/my_new_skill.py`
    2. Subclass BaseSkill and implement `execute()`
    3. Call `registry.discover()` — the skill is automatically available.
    No changes needed in agents or the registry itself.

Usage:
    registry = SkillRegistry()
    registry.discover()
    skill = registry.instantiate("pdf_reader", config={"max_pages": 100})
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional, Type

from utils.logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:
    """
    Thread-safe singleton registry for BaseSkill subclasses.

    Usage is intentionally simple:
        registry = SkillRegistry()         # always returns the same instance
        registry.discover()                # auto-scan skills/
        skill = registry.instantiate("pdf_reader")
    """

    _instance: Optional[SkillRegistry] = None

    def __new__(cls) -> SkillRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._registry: Dict[str, Type] = {}
        return cls._instance

    # ── Discovery ──────────────────────────────────────────────────────

    def discover(self) -> None:
        """
        Scan the `skills` package and register all concrete BaseSkill subclasses.
        Safe to call multiple times; re-discovers on each call.
        """
        from skills.base_skill import BaseSkill  # local import avoids circular
        import skills as skills_pkg

        pkg_dir = str(Path(skills_pkg.__file__).parent)
        self._registry.clear()

        for _, module_name, _is_pkg in pkgutil.iter_modules([pkg_dir]):
            if module_name in ("base_skill",):
                continue
            try:
                module = importlib.import_module(f"skills.{module_name}")
                for _attr_name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, BaseSkill)
                        and obj is not BaseSkill
                        and not inspect.isabstract(obj)
                        and obj.__module__ == module.__name__  # only own definitions
                    ):
                        instance = obj()
                        if not instance.name:
                            logger.warning(
                                f"Skill class '{obj.__name__}' has no `name` set — skipped."
                            )
                            continue
                        self._registry[instance.name] = obj
                        logger.debug(f"Registered skill: '{instance.name}' ({obj.__name__})")
            except Exception as exc:
                logger.warning(
                    f"Could not import skill module 'skills.{module_name}': {exc}"
                )

        logger.info(
            f"SkillRegistry ready — {len(self._registry)} skills: {self.list_skills()}"
        )

    # ── Registration ───────────────────────────────────────────────────

    def register(self, skill_cls: Type) -> None:
        """Manually register a skill class (useful for testing with mock skills)."""
        instance = skill_cls()
        self._registry[instance.name] = skill_cls
        logger.debug(f"Manually registered skill: '{instance.name}'")

    # ── Retrieval ──────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Type]:
        """Return the skill class by name, or None if not found."""
        return self._registry.get(name)

    def instantiate(self, name: str, config: Optional[Dict] = None) -> Optional[object]:
        """
        Instantiate a skill by name with optional config dict.
        Returns None and logs an error if the skill is not registered.
        """
        cls = self._registry.get(name)
        if cls is None:
            logger.error(
                f"Skill '{name}' not in registry. Available: {self.list_skills()}"
            )
            return None
        return cls(config=config or {})

    def list_skills(self) -> List[str]:
        """Return sorted list of all registered skill names."""
        return sorted(self._registry.keys())

    def reset(self) -> None:
        """Clear registry — useful in tests."""
        self._registry.clear()
        logger.debug("SkillRegistry reset.")

    def __repr__(self) -> str:
        return f"<SkillRegistry skills={self.list_skills()}>"
