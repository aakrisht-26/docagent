"""
StructureRecognitionSkill — uses PaddleOCR's PP-Structure to parse complex tables layout.
Automatically detects GPU availability and falls back to CPU if no GPU is present.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from core.models import DocumentChunk, ParsedDocument, SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)


class StructureRecognitionSkill(BaseSkill):
    """
    Identifies and extracts highly complex tables from given PDFs using PaddleOCR's PPStructure.
    Only intended to be used on domains that heavily feature tables (Technical, Scientific, Financial).
    
    Config keys:
        use_gpu (bool): Whether to use GPU acceleration (default: True).
        show_log (bool): Print paddle logs (default: False).
    """

    name = "structure_recognition"
    description = "Extracts highly structured tables from complex domain PDFs."
    required_inputs = ["parsed_document", "file_path"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        # Load the engine lazily to save VRAM when not in use
        self._engine = None

    def _detect_gpu(self) -> bool:
        """
        Return True only if PaddlePaddle is CUDA-compiled AND at least one GPU
        is visible to it.

        NOTE: the CPU-only `paddlepaddle` wheel returns False here even when the
        machine physically has a GPU. The GPU build (`paddlepaddle-gpu`, matching
        the installed CUDA version) must be installed for acceleration to work.
        This is the usual reason PP-Structure runs on CPU (15-25 min/page) despite
        the laptop having a GPU.
        """
        try:
            import paddle
            if not paddle.device.is_compiled_with_cuda():
                return False
            return paddle.device.cuda.device_count() > 0
        except Exception as exc:  # paddle missing, driver mismatch, etc.
            self.logger.debug(f"GPU detection failed ({exc}); assuming no GPU.")
            return False

    @staticmethod
    def _extract_table_html(res, res_idx: int) -> str:
        """
        Extract HTML from a single result entry, handling both API versions:
          v3.x: result object with result["layout_parsing_res"] list of
                dicts {block_label, block_content, block_bbox}
          v2.x: plain dict  {type, res: {html, ...}}
        Returns the HTML string or "" if no table found.
        """
        # ── PaddleOCR v3.x ────────────────────────────────────────────
        # result is a page-level object; each block is in layout_parsing_res
        try:
            blocks = res["layout_parsing_res"]
            for block in blocks:
                if block.get("block_label") == "table":
                    html = block.get("block_content", "")
                    if html:
                        return html
            return ""
        except (KeyError, TypeError):
            pass

        # ── PaddleOCR v2.x ────────────────────────────────────────────
        if isinstance(res, dict):
            if res.get("type") == "table":
                return res.get("res", {}).get("html", "")
        return ""

    def _get_engine(self):
        if self._engine is None:
            self.logger.info("Loading PaddleOCR PP-Structure Engine...")
            import os
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            try:
                from paddleocr import PPStructureV3
            except ImportError as exc:
                raise RuntimeError(
                    "PaddleOCR not installed. Run: pip install paddleocr paddlepaddle"
                ) from exc
            import logging
            logging.getLogger("ppocr").setLevel(logging.ERROR)
            # PaddleOCR v3.x removed `show_log` and `use_gpu` (GPU is auto-detected;
            # logs are suppressed via the ppocr logger above). These arguments now
            # raise ValueError. Fall back to no-args if `lang` also causes issues.
            try:
                self._engine = PPStructureV3(lang="en")
            except ValueError:
                self._engine = PPStructureV3()
        return self._engine

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()
        
        parsed_doc: ParsedDocument = inputs.data["parsed_document"]
        file_path = Path(inputs.data["file_path"])
        domain = inputs.data.get("domain", "General")
        
        # Guard clause: We only do this computationally expensive pass if it's structural
        target_domains = ["Technical", "Financial", "Research", "Scientific"]
        if domain not in target_domains:
            self.logger.info(f"Skipping Structure Recognition (Domain '{domain}' does not require intensive table parsing).")
            return SkillOutput(success=True, data=parsed_doc)

        # ── GPU gate ───────────────────────────────────────────────────────
        # PP-Structure is a heavy deep-learning model. On a CUDA GPU it runs in
        # ~seconds per page; on CPU it can take 15-25 MINUTES per page, which
        # makes the whole pipeline appear frozen on multi-page documents. So we
        # only run it when PaddlePaddle can actually see a CUDA GPU. Set
        # `pdf.allow_cpu_structure: true` to force CPU processing anyway.
        gpu_ok = self._detect_gpu()
        allow_cpu = bool(self.config.get("allow_cpu_structure", False))
        if not gpu_ok and not allow_cpu:
            self.logger.warning(
                "No CUDA-capable GPU detected by PaddlePaddle — skipping high-fidelity "
                "table extraction (PP-Structure) to avoid extremely slow CPU processing. "
                "Basic table extraction from the parser still applies. To enable GPU "
                "acceleration, install the GPU build (`pip install paddlepaddle-gpu`) that "
                "matches your CUDA version. To force CPU anyway, set "
                "`pdf.allow_cpu_structure: true` in configs/default.yaml."
            )
            return SkillOutput(
                success=True,
                data=parsed_doc,
                duration_ms=(time.monotonic() - start) * 1000,
            )

        device_note = "GPU" if gpu_ok else "CPU (forced via allow_cpu_structure)"
        self.logger.info(f"Initiating High-Fidelity Table Search for {domain} document [{device_note}].")

        try:
            import fitz
            import cv2
            import numpy as np
        except ImportError:
            self.logger.error("PyMuPDF (fitz) or cv2 missing for Structure Recognition.")
            return SkillOutput(success=False, data=None, error="Missing dependencies: fitz, cv2")

        # Initialise engine — skip gracefully if deps are missing (RuntimeError)
        try:
            engine = self._get_engine()
        except RuntimeError as e:
            self.logger.warning(f"Structure recognition unavailable, skipping: {e}")
            return SkillOutput(
                success=True, data=parsed_doc,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            self.logger.error(f"PP-Structure engine init failed: {e}")
            return SkillOutput(success=False, data=parsed_doc, error=str(e))

        with fitz.open(str(file_path)) as doc:
            extracted_tables = []
            new_chunks = []

            for i, chunk in enumerate(parsed_doc.chunks):
                # Convert page directly to high-res image
                page_index = min(max(int(chunk.page_or_sheet) - 1, 0), len(doc) - 1)
                page = doc[page_index]

                # Use 200 DPI to save VRAM but maintain cell integrity
                pix = page.get_pixmap(dpi=200)
                img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

                if pix.n == 4:
                    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
                elif pix.n == 3:
                    img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                else:
                    img_cv = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)

                self.logger.debug(f"  PP-Structure scanning page {page_index + 1}...")
                try:
                    page_results = engine.predict(img_cv)
                except Exception:
                    page_results = engine(img_cv)

                table_markdown_blocks = []

                for res_idx, res in enumerate(page_results):
                    table_html = self._extract_table_html(res, res_idx)
                    if table_html:
                        table_str = f"[HIGH-FIDELITY TABLE - PAGE {page_index + 1}]\n{table_html}\n"
                        table_markdown_blocks.append(table_str)
                        extracted_tables.append({
                            "page": page_index + 1,
                            "index": res_idx,
                            "html": table_html,
                        })
                
                # If we found high spatial tables, we append them to the existing chunk
                # This allows the summarize skill to read the perfect HTML table rather than garbled strings
                if table_markdown_blocks:
                    new_text = chunk.text + "\n\n" + "\n".join(table_markdown_blocks)
                    self.logger.info(f"  Found {len(table_markdown_blocks)} HD tables on page {page_index + 1}")
                else:
                    new_text = chunk.text
                    
                new_chunks.append(DocumentChunk(
                    text=new_text,
                    page_or_sheet=chunk.page_or_sheet,
                    chunk_index=chunk.chunk_index,
                    metadata={**chunk.metadata, "hd_tables": len(table_markdown_blocks)}
                ))

        # Update the parsed document with improved text and explicit tables list
        new_parsed_doc = ParsedDocument(
            file_name=parsed_doc.file_name,
            file_type=parsed_doc.file_type,
            chunks=new_chunks,
            full_text="\n\n".join(c.text for c in new_chunks),
            tables=parsed_doc.tables + extracted_tables, # append new tables
            metadata={**parsed_doc.metadata, "pp_structure": True},
            page_count=parsed_doc.page_count
        )

        return SkillOutput(
            success=True,
            data=new_parsed_doc,
            duration_ms=(time.monotonic() - start) * 1000
        )
