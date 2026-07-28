"""
PDFReaderSkill — extracts text, tables, and metadata from PDF files.

Engine priority:
    1. pdfplumber  — best for text-layer PDFs (preserves layout, extracts tables)
    2. PyMuPDF     — fallback for complex/encrypted layouts
    3. Tesseract   — OCR fallback for scanned/image-only PDFs

All engines produce the same ParsedDocument output, ensuring
downstream skills are engine-agnostic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import DocumentChunk, ParsedDocument, SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)


class PDFReaderSkill(BaseSkill):
    """
    Reads and parses PDF documents into a structured ParsedDocument.

    Config keys:
        use_ocr_fallback (bool)  : Enable Tesseract OCR for scanned PDFs (default: True)
        ocr_language     (str)   : Tesseract language code (default: "eng")
        extract_tables   (bool)  : Extract tables separately (default: True)
        max_pages        (int)   : Maximum pages to process (default: 500)
    """

    name = "pdf_reader"
    description = "Extracts text, tables, and metadata from PDFs. Supports OCR for scanned docs."
    required_inputs = ["file_path"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._use_ocr      = self.get_config("use_ocr_fallback", True)
        self._ocr_lang     = self.get_config("ocr_language", "eng")
        self._extract_tbl  = self.get_config("extract_tables", True)
        self._max_pages    = self.get_config("max_pages", 500)
        
        # Windows-specific Tesseract path discovery
        import os
        import platform
        self._tesseract_path = self.get_config("tesseract_path", "")
        
        if not self._tesseract_path and platform.system() == "Windows":
            common_paths = [
                os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Tesseract-OCR", "tesseract.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR", "tesseract.exe"),
            ]
            for p in common_paths:
                if os.path.exists(p):
                    self._tesseract_path = p
                    break

    # ── Skill entry point ─────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()
        file_path = Path(inputs.data["file_path"])

        if not file_path.exists():
            return SkillOutput(success=False, data=None,
                               error=f"File not found: {file_path}")

        # Try primary logic (pdfplumber -> fitz)
        doc: Optional[ParsedDocument] = None
        try:
            doc = self._parse_pdfplumber(file_path)
            self.logger.info(f"pdfplumber extracted {doc.word_count} words from {doc.page_count} pages")
        except Exception as exc:
            self.logger.warning(f"pdfplumber failed ({exc}); trying PyMuPDF fallback.")
            try:
                doc = self._parse_fitz(file_path)
                self.logger.info(f"PyMuPDF extracted {doc.word_count} words")
            except Exception as exc2:
                return SkillOutput(
                    success=False, data=None,
                    error=f"Both PDF engines failed. pdfplumber: {exc}. PyMuPDF: {exc2}",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        # OCR escalation ONLY for binary empty documents
        warnings: List[str] = []
        if doc.is_empty and self._use_ocr:
            self.logger.info("No text layer found — escalating to high-quality OCR.")
            doc = self._parse_ocr(file_path, fallback_doc=doc)
            if doc.is_empty:
                warnings.append(
                    "OCR produced no text. The document may be encrypted, "
                    "password-protected, or contain unsupported image formats."
                )
        elif doc.is_empty:
            warnings.append(
                "No text extracted (OCR fallback is disabled). "
                "Enable use_ocr_fallback=true in configs/default.yaml for scanned PDFs."
            )

        return SkillOutput(
            success=True,
            data=doc,
            warnings=warnings,
            duration_ms=(time.monotonic() - start) * 1000,
            metadata={"engine": doc.metadata.get("engine", "pdfplumber"),
                      "pages": doc.page_count},
        )

    # ── Engines ───────────────────────────────────────────────────────

    def _parse_pdfplumber(self, file_path: Path) -> ParsedDocument:
        """Primary parser — best text/table extraction for standard PDFs."""
        import pdfplumber
        import re

        chunks: List[DocumentChunk] = []
        tables: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        raw_meta: Dict[str, Any] = {}
        
        def not_in_bboxes(obj, bboxes):
            """Checks if a pdfplumber object overlaps with any bounding boxes."""
            obj_type = obj.get("object_type")
            if obj_type not in ("char", "image", "rect", "line"):
                return True
            x0, y0, x1, y1 = obj.get("x0", 0), obj.get("top", 0), obj.get("x1", 0), obj.get("bottom", 0)
            for box in bboxes:
                X0, Y0, X1, Y1 = box
                if not (x1 <= X0 or x0 >= X1 or y1 <= Y0 or y0 >= Y1):
                    return False
            return True

        with pdfplumber.open(str(file_path)) as pdf:
            raw_meta = {k: str(v) for k, v in (pdf.metadata or {}).items()}
            page_count = len(pdf.pages)

            for i, page in enumerate(pdf.pages[: self._max_pages]):
                tables_bboxes = []
                formatted_tables = []

                # Table extraction & Masking
                if self._extract_tbl:
                    found_tables = page.find_tables()
                    tables_bboxes = [t.bbox for t in found_tables]
                    
                    for j, table in enumerate(found_tables):
                        raw_table = table.extract()
                        if not raw_table: continue
                        rows = []
                        for row in raw_table:
                            cells = [str(c).strip() if c is not None else "" for c in row]
                            rows.append(" | ".join(cells))
                        tbl_text = "\n".join(rows)
                        if tbl_text.strip():
                            tables.append({
                                "page": i + 1, "index": j,
                                "text": tbl_text, "data": raw_table,
                            })
                            formatted_tables.append(f"\n[TABLE — Page {i + 1}, #{j + 1}]\n{tbl_text}")

                # Mask out tables from the text layer so they don't get extracted twice
                text_page = page.filter(lambda obj: not_in_bboxes(obj, tables_bboxes))
                
                # Extract text using layout preservation for vastly cleaner horizontal alignment
                page_text = text_page.extract_text(layout=True) or ""
                
                # Clean up extreme whitespaces introduced by layout=True
                page_text = re.sub(r'[ \t]{3,}', '  ', page_text)
                page_text = re.sub(r'\n[ \t]+\n', '\n\n', page_text)

                combined_page_text = page_text.strip() + "".join(formatted_tables)
                text_parts.append(combined_page_text)
                
                chunks.append(DocumentChunk(
                    text=combined_page_text,
                    page_or_sheet=i + 1,
                    chunk_index=i,
                    metadata={"page": i + 1, "engine": "pdfplumber"},
                ))

        raw_meta["engine"] = "pdfplumber"
        return ParsedDocument(
            file_name=file_path.name,
            file_type="pdf",
            chunks=chunks,
            full_text="\n\n".join(filter(None, text_parts)),
            tables=tables,
            metadata=raw_meta,
            page_count=page_count,
        )

    def _parse_fitz(self, file_path: Path) -> ParsedDocument:
        """Fallback parser — handles complex layouts, encrypted PDFs."""
        import fitz  # PyMuPDF

        chunks: List[DocumentChunk] = []
        text_parts: List[str] = []

        with fitz.open(str(file_path)) as doc:
            page_count = len(doc)

            for i, page in enumerate(doc[: self._max_pages]):
                text = page.get_text("text") or ""  # type: ignore[attr-defined]
                text_parts.append(text)
                chunks.append(DocumentChunk(
                    text=text,
                    page_or_sheet=i + 1,
                    chunk_index=i,
                    metadata={"page": i + 1, "engine": "fitz"},
                ))

        return ParsedDocument(
            file_name=file_path.name,
            file_type="pdf",
            chunks=chunks,
            full_text="\n\n".join(filter(None, text_parts)),
            tables=[],
            metadata={"engine": "fitz"},
            page_count=page_count,
        )

    def _parse_ocr(self, file_path: Path, fallback_doc: ParsedDocument) -> ParsedDocument:
        """
        OCR engine: PyMuPDF renders pages to images, OpenCV pre-processes, Tesseract reads them.
        Requires: PyMuPDF, pytesseract, OpenCV, numpy and Tesseract binary installed.
        """
        try:
            import fitz
            import pytesseract
            import cv2
            import numpy as np
            
            # Application of found tesseract path
            if self._tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = self._tesseract_path
            else:
                # Basic check to see if it's in PATH, otherwise it will throw an error eventually
                pass
        except ImportError as exc:
            self.logger.warning(
                f"OCR dependencies not installed ({exc}). "
                "Install PyMuPDF, pytesseract, numpy and opencv-python-headless — see README."
            )
            return fallback_doc

        chunks: List[DocumentChunk] = []
        text_parts: List[str] = []

        with fitz.open(str(file_path)) as doc:
            page_count = len(doc)
            if page_count > self._max_pages:
                self.logger.warning(
                    f"PDF has {page_count} pages; OCR capped at {self._max_pages}. "
                    "Increase pdf.max_pages in config to process the full document."
                )
            self.logger.info(f"OCR processing {min(page_count, self._max_pages)} pages with Adaptive CV (lang={self._ocr_lang})")

            for i, page in enumerate(doc[: self._max_pages]):
                self.logger.debug(f"  OCR page {i + 1}/{page_count}")
                try:
                    # Step 1: Render at 400 DPI instead of 300 for clear geometry extraction
                    pix = page.get_pixmap(dpi=400)  # type: ignore[attr-defined]

                    # Step 2: Convert PyMuPDF pixmap to numpy array for OpenCV
                    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

                    # Step 3: Convert to Grayscale
                    if pix.n == 4:
                        gray = cv2.cvtColor(img_array, cv2.COLOR_RGBA2GRAY)
                    elif pix.n == 3:
                        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                    else:
                        gray = img_array

                    # Step 4: Apply Gaussian Blur to reduce ISO noise inherent in bad lighting
                    blur = cv2.GaussianBlur(gray, (5, 5), 0)

                    # Step 5: Adaptive Thresholding
                    # Crucial for low-light/shadows: analyzes lighting context locally to create pure black & white.
                    thresh = cv2.adaptiveThreshold(
                        blur, 255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        15, 6
                    )

                    # Step 5b: Deskewing (Perspective correction for photos)
                    # Find all white pixels to determine text orientation
                    coords = np.column_stack(np.where(thresh == 0))
                    if len(coords) > 0:
                        angle = cv2.minAreaRect(coords)[-1]
                        # Adjust angle for minAreaRect quirks
                        if angle < -45: angle = -(90 + angle)
                        else: angle = -angle

                        if abs(angle) > 0.5 and abs(angle) < 15: # Only deskew if slightly tilted
                            (h, w) = thresh.shape[:2]
                            center = (w // 2, h // 2)
                            M = cv2.getRotationMatrix2D(center, angle, 1.0)
                            thresh = cv2.warpAffine(thresh, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

                    # Step 6: Optimized Tesseract Extraction (PSM 3 handles multi-line blocks perfectly)
                    text = pytesseract.image_to_string(thresh, lang=self._ocr_lang, config="--psm 3 --oem 3")
                except Exception as exc:
                    self.logger.warning(f"OCR failed on page {i + 1}: {exc}. Skipping page.")
                    text = ""

                text_parts.append(text)
                chunks.append(DocumentChunk(
                    text=text,
                    page_or_sheet=i + 1,
                    chunk_index=i,
                    metadata={"page": i + 1, "engine": "ocr_tesseract_cv_adaptive"},
                ))

        return ParsedDocument(
            file_name=file_path.name,
            file_type="pdf",
            chunks=chunks,
            full_text="\n\n".join(filter(None, text_parts)),
            tables=[],
            metadata={**fallback_doc.metadata, "engine": "ocr_tesseract", "ocr": True},
            page_count=page_count,
        )

    def _is_garbage_text(self, text: str) -> bool:
        """Determines if extracted text is likely junk/bad OCR."""
        if not text or len(text.strip()) < 20:
            return False
            
        # Check for heavy non-alphanumeric noise (common in bad hidden OCR layers)
        alnum = sum(1 for c in text if c.isalnum() or c.isspace())
        ratio = alnum / len(text)
        if ratio < 0.6: # More than 40% noise characters
            return True
            
        # Check for lack of basic punctuation or whitespace structure
        if " " not in text and len(text) > 50:
            return True
            
        return False

