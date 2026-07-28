"""
Configuration management for DocAgent.

Environment Variable Overrides (all optional):
    DOCAGENT_MAX_FILE_MB    : int
    GROQ_API_KEY            : str  (Groq API Key)
    DOCAGENT_GROQ_MODEL     : str  (e.g. llama-3.3-70b-versatile)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# Matches lines of the form KEY=value or KEY = value (used to detect new entries)
_KEY_VALUE_LINE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*=')


def _read_multiline_env_keys() -> list:
    """
    Parse GROQ_API_KEYS from the .env file with multiline support.

    python-dotenv stops at the first newline for unquoted values, so
    ``os.environ.get("GROQ_API_KEYS")`` only contains the first physical line
    when the value is spread across multiple lines without quotes.  This
    function reads the raw .env file and stitches together any continuation
    lines (lines that don't look like a new KEY=VALUE pair) so all keys are
    captured regardless of how the user formatted the file.
    """
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return []

    collecting = False
    raw_parts: list = []

    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.upper().startswith("GROQ_API_KEYS="):
                    collecting = True
                    raw_parts.append(stripped[len("GROQ_API_KEYS="):])
                elif collecting:
                    # Stop on blank line, comment, or a new KEY=VALUE pair
                    if not stripped or stripped.startswith("#") or _KEY_VALUE_LINE_RE.match(stripped):
                        break
                    raw_parts.append(stripped)
    except OSError:
        return []

    if not raw_parts:
        return []

    raw_value = ",".join(raw_parts)
    return [k.strip() for k in raw_value.split(",") if k.strip()]

_CONFIG_PATH = Path(__file__).parent.parent / "configs" / "default.yaml"
_CONFIG_CACHE: Optional[AppConfig] = None


@dataclass
class GroqConfig:
    """Settings for the Groq Cloud API."""
    enabled: bool = True
    api_keys: str = ""
    api_key: str = "" # Keep for backward compatibility
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "llama-3.3-70b-versatile"
    timeout_seconds: int = 180
    temperature: float = 0.15


@dataclass
class PDFConfig:
    use_ocr_fallback: bool = True
    ocr_language: str = "eng"
    extract_tables: bool = True
    max_pages: int = 500


@dataclass
class ExcelConfig:
    max_sheets: int = 50
    include_formulas: bool = False
    max_rows_per_sheet: int = 10000


@dataclass
class SummarizationConfig:
    chunk_size: int = 4000
    chunk_overlap: int = 300
    max_summary_length: int = 3000
    extractive_sentences: int = 15


@dataclass
class ClassificationConfig:
    questionnaire_threshold: float = 0.35


@dataclass
class QuestionExtractionConfig:
    min_questions: int = 1
    dedup: bool = True
    max_questions: int = 200


@dataclass
class ExportConfig:
    pdf_font_size: int = 11
    pdf_margin_inch: float = 0.9


@dataclass
class AppConfig:
    name: str = "DocAgent"
    version: str = "1.0.0"
    max_file_size_mb: int = 50
    log_level: str = "INFO"
    log_file: str = "logs/docagent.log"

    groq: GroqConfig = field(default_factory=GroqConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    summarization: SummarizationConfig = field(default_factory=SummarizationConfig)
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    question_extraction: QuestionExtractionConfig = field(default_factory=QuestionExtractionConfig)
    export: ExportConfig = field(default_factory=ExportConfig)

    def validate(self) -> list:
        """
        Check configuration for common problems.

        Returns a list of human-readable warning strings (empty = all OK).
        Does NOT raise — callers decide what to do with the warnings.
        """
        issues: list = []

        resolved_keys = resolve_groq_api_keys({"api_keys": self.groq.api_keys, "api_key": self.groq.api_key})
        if not resolved_keys:
            issues.append(
                "No Groq API key configured. LLM-based summarization and question extraction "
                "will fall back to extractive mode. Set the GROQ_API_KEY environment variable "
                "or add it to configs/default.yaml."
            )

        if self.max_file_size_mb <= 0:
            issues.append(f"max_file_size_mb is {self.max_file_size_mb} — must be > 0.")

        if not self.groq.base_url.startswith("http"):
            issues.append(
                f"groq.base_url '{self.groq.base_url}' does not look like a valid URL."
            )

        return issues

    def to_dict(self) -> Dict[str, Any]:
        return {
            "groq": {
                "enabled": self.groq.enabled,
                "api_keys": self.groq.api_keys,
                "api_key": self.groq.api_key,
                "base_url": self.groq.base_url,
                "model": self.groq.model,
                "timeout_seconds": self.groq.timeout_seconds,
                "temperature": self.groq.temperature,
            },
            "pdf": {
                "use_ocr_fallback": self.pdf.use_ocr_fallback,
                "ocr_language": self.pdf.ocr_language,
                "extract_tables": self.pdf.extract_tables,
                "max_pages": self.pdf.max_pages,
            },
            "excel": {
                "max_sheets": self.excel.max_sheets,
                "include_formulas": self.excel.include_formulas,
                "max_rows_per_sheet": self.excel.max_rows_per_sheet,
            },
            "summarization": {
                "chunk_size": self.summarization.chunk_size,
                "chunk_overlap": self.summarization.chunk_overlap,
                "max_summary_length": self.summarization.max_summary_length,
                "extractive_sentences": self.summarization.extractive_sentences,
            },
            "classification": {
                "questionnaire_threshold": self.classification.questionnaire_threshold,
            },
            "question_extraction": {
                "dedup": self.question_extraction.dedup,
                "max_questions": self.question_extraction.max_questions,
            },
            "export": {
                "pdf_font_size": self.export.pdf_font_size,
                "pdf_margin_inch": self.export.pdf_margin_inch,
            },
        }


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    path = config_path or _CONFIG_PATH
    raw: Dict[str, Any] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        pass
    except yaml.YAMLError as exc:
        import warnings
        warnings.warn(f"Could not parse config file '{path}': {exc}. Using defaults.")
        raw = {}

    app_r = raw.get("app", {})
    gro_r = raw.get("groq", {})
    pdf_r = raw.get("pdf", {})
    xls_r = raw.get("excel", {})
    sum_r = raw.get("summarization", {})
    cls_r = raw.get("classification", {})
    q_r   = raw.get("question_extraction", {})
    exp_r = raw.get("export", {})

    cfg = AppConfig(
        name            = app_r.get("name", "DocAgent"),
        version         = app_r.get("version", "1.0.0"),
        max_file_size_mb= int(os.getenv("DOCAGENT_MAX_FILE_MB", app_r.get("max_file_size_mb", 50))),
        log_level       = os.getenv("DOCAGENT_LOG_LEVEL", app_r.get("log_level", "INFO")),
        log_file        = os.getenv("DOCAGENT_LOG_FILE", app_r.get("log_file", "logs/docagent.log")),
        groq=GroqConfig(
            enabled        = _env_bool("DOCAGENT_GROQ_ENABLED", gro_r.get("enabled", True)),
            api_keys       = os.getenv("GROQ_API_KEYS", gro_r.get("api_keys", "")),
            api_key        = os.getenv("GROQ_API_KEY", gro_r.get("api_key", "")),
            base_url       = os.getenv("DOCAGENT_GROQ_URL", gro_r.get("base_url", "https://api.groq.com/openai/v1")),
            model          = os.getenv("DOCAGENT_GROQ_MODEL", gro_r.get("model", "llama-3.3-70b-versatile")),
            timeout_seconds= int(os.getenv("DOCAGENT_GROQ_TIMEOUT", gro_r.get("timeout_seconds", 180))),
            temperature    = float(gro_r.get("temperature", 0.15)),
        ),
        pdf  = PDFConfig(**{k: v for k, v in pdf_r.items() if k in PDFConfig.__dataclass_fields__}) if pdf_r else PDFConfig(),
        excel= ExcelConfig(**{k: v for k, v in xls_r.items() if k in ExcelConfig.__dataclass_fields__}) if xls_r else ExcelConfig(),
        summarization        = SummarizationConfig(**{k: v for k, v in sum_r.items() if k in SummarizationConfig.__dataclass_fields__}) if sum_r else SummarizationConfig(),
        classification       = ClassificationConfig(**{k: v for k, v in cls_r.items() if k in ClassificationConfig.__dataclass_fields__}) if cls_r else ClassificationConfig(),
        question_extraction  = QuestionExtractionConfig(**{k: v for k, v in q_r.items() if k in QuestionExtractionConfig.__dataclass_fields__}) if q_r else QuestionExtractionConfig(),
        export               = ExportConfig(**{k: v for k, v in exp_r.items() if k in ExportConfig.__dataclass_fields__}) if exp_r else ExportConfig(),
    )

    _CONFIG_CACHE = cfg
    return cfg


def reset_config_cache() -> None:
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def resolve_groq_api_keys(groq_cfg: Optional[Dict[str, Any]] = None) -> list:
    """
    Collect all Groq API keys from every source, deduplicated, in priority order.

    Sources checked (all combined — no source shadows another):
      1. GROQ_API_KEYS env var  (comma-separated, intended for multiple keys)
      2. groq_cfg["api_keys"]   (YAML config field, also comma-separated)
      3. GROQ_API_KEY env var   (single-key env var)
      4. groq_cfg["api_key"]    (YAML legacy single-key field)

    Returns a deduplicated list of non-empty stripped key strings.
    All sources are combined so that setting GROQ_API_KEY=key1 does NOT
    prevent multiple keys configured in api_keys from being used.
    """
    groq_cfg = groq_cfg or {}

    def _split(raw) -> list:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(k).strip() for k in raw if str(k).strip()]
        return [k.strip() for k in str(raw).split(",") if k.strip()]

    seen: set = set()
    keys: list = []
    for src in (
        os.environ.get("GROQ_API_KEYS", ""),
        groq_cfg.get("api_keys", ""),
        os.environ.get("GROQ_API_KEY", ""),
        groq_cfg.get("api_key", ""),
    ):
        for k in _split(src):
            if k not in seen:
                seen.add(k)
                keys.append(k)

    # Supplement with raw .env file parse — handles unquoted multiline values
    # that python-dotenv truncates to the first physical line.
    for k in _read_multiline_env_keys():
        if k not in seen:
            seen.add(k)
            keys.append(k)

    return keys


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")
