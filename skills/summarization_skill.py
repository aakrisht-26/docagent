"""
SummarizationSkill -- generates detailed, structured summaries of document text.

Key improvements:
  - Uses unified LLMClient (Grok API first, Ollama fallback)
  - Section-aware chunking: detects headings and keeps sections intact
  - Structured output: fixed 4-section schema in every LLM prompt
  - Map phase extracts bullet-point facts; Reduce phase synthesises them
  - Extractive fallback: heading-boosted + position-weighted sentence scoring
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Stopwords ─────────────────────────────────────────────────────────────────
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "was", "are", "were",
    "be", "been", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "that", "this", "it",
    "he", "she", "they", "we", "you", "i", "not", "no", "can", "as",
    "so", "if", "then", "also", "its", "their", "our", "your", "my",
    "his", "her", "than", "more", "about", "which", "who", "what",
    "when", "where", "how", "all", "any", "both", "each", "few",
    "into", "through", "during", "before", "after", "above", "below",
    "just", "because", "while", "there", "very", "too", "only", "up",
    "out", "over", "such", "being", "between", "here", "them", "these",
    "those", "per", "etc", "eg", "ie", "said", "says", "one", "two",
})

# ── Summary length config ─────────────────────────────────────────────────────
_LENGTH_CONFIGS: Dict[str, Dict[str, Any]] = {
    "Concise":    {"max_tokens": 800,  "instruction": "Write 1-2 tight paragraphs only. Lead with the single most important finding. No sub-headings."},
    "Standard":   {"max_tokens": 3000, "instruction": ""},
    "Detailed":   {"max_tokens": 5000, "instruction": "Cover every major section with sub-headings and supporting evidence. Include key figures."},
    # 6000, not 8000. Measured across three documents, Exhaustive's longest
    # output was 3783 tokens -- it never came within 4000 of its old ceiling,
    # because on the map-reduce path length is driven by the DIRECTIVE and not
    # by max_tokens. Only Standard binds (3722 against 4024). So 8000 bought no
    # extra words and cost a larger request, which is what met the free tier's
    # rolling per-minute window and 413'd.
    #
    # 6000 keeps Exhaustive strictly the largest budget, sits ~2200 tokens above
    # the longest output ever observed, and shrinks the request by 976 tokens.
    # It does NOT guarantee acceptance -- see _PROVIDER_REQUEST_CEILING.
    "Exhaustive": {"max_tokens": 6000, "instruction": "Produce a comprehensive report covering every claim, data point, entity, and timeline mentioned."},
}

# ── Reasoning allowance ───────────────────────────────────────────────────────
#
# The numbers above are CONTENT budgets: what a "Standard" summary is meant to
# be allowed to say. `max_tokens` is not that. It caps content PLUS the
# reasoning tokens a model spends before writing anything, so under
# gpt-oss-120b every preset silently delivered less than it was designed to.
# The allowance is added on top at the call, which restores what the preset
# always meant. Raising the preset numbers themselves would change the meaning.
#
# SIZED FROM MEASUREMENT, and the shape of the measurement matters:
#
#   reasoning vs INPUT size   (Standard, no directive)
#     prompt   448 -> reasoning  45
#     prompt  2460 -> reasoning  42
#     prompt  1575 -> reasoning 192
#   A 5.5x range of input moves reasoning barely at all. It does NOT scale
#   with input, so the allowance does not need to.
#
#   reasoning vs REQUESTED OUTPUT   (same document, ~1600-token prompt)
#     Concise      199
#     Standard      34
#     Detailed     126
#     Exhaustive   902
#   Here it does move, and the directive is what moves it — "cover every claim,
#   data point, entity and timeline" costs an order of magnitude more thinking
#   than no directive at all.
#
# So the allowance is preset-SENSITIVE in principle. It is a flat constant
# anyway, because the same measurement is noisy run to run: Standard produced
# 34 reasoning tokens in one run and 192 in another on identical input. Fitting
# a per-preset curve to four noisy points would imply a precision the data does
# not support, and a flat value that clears the worst observation is honest
# about what is known. 1024 clears 902 with margin.
_REASONING_ALLOWANCE = 1024

# Content budget for one map-step chunk. Measured need on the largest real
# chunks of sample_large_report: ~943 content tokens.
_MAP_CONTENT_TOKENS = 1000


# Groq counts prompt + max_tokens against the per-minute allowance and REFUSES
# the request outright with a 413 when the sum exceeds what is LEFT in the
# window. It does not truncate.
#
# THIS IS A ROLLING WINDOW, NOT A PER-REQUEST SIZE CAP. An earlier version of
# this comment said the latter and sized the clamp accordingly; measurement
# disproved it. The same key accepted a 34,072-token request and refused an
# 8,600-token one minutes later, and two sessions disagreed about which keys
# refuse what. What a refusal reports is how much budget that key had left at
# that instant.
#
# So NO VALUE HERE IS SAFE, and this clamp does not make one safe. What makes a
# refusal survivable is that the client now rotates to another key on a 413
# rather than returning None (see `_run_with_rotation`). Before that, one
# unlucky key selection dropped the whole summary to extractive in silence.
#
# The clamp remains for a narrower reason: the reasoning allowance must not push
# a request past what any key could ever serve. 8000 is the smallest per-key
# limit seen across the configured keys, so it is the point beyond which a
# request is refused everywhere rather than merely somewhere.
_PROVIDER_REQUEST_CEILING = 8000


def _with_reasoning_room(content_tokens: int) -> int:
    """Turn a CONTENT budget into a max_tokens that can actually deliver it.

    Clamped to what the provider will accept, so a preset that already fitted
    is not pushed over the edge by the allowance.
    """
    return min(content_tokens + _REASONING_ALLOWANCE,
               max(content_tokens, _PROVIDER_REQUEST_CEILING))


# ── Audience tone personas ─────────────────────────────────────────────────────
_TONE_PERSONAS: Dict[str, Dict[str, str]] = {
    "Expert / Technical": {
        "system_role": "You are a domain expert writing for a technical peer audience.",
        "style": "Use precise technical vocabulary and domain jargon. Assume graduate-level familiarity. Include methodological critique and quantitative rigour.",
    },
    "Professional": {
        "system_role": "You are a Senior {domain} Analyst.",
        "style": "",
    },
    "General / Non-technical": {
        "system_role": "You are an expert at explaining complex topics in plain, accessible English.",
        "style": "Avoid jargon. Use analogies and short sentences. Define any technical terms you must use.",
    },
    "Student": {
        "system_role": "You are a knowledgeable tutor writing for an undergraduate student.",
        "style": "Be educational. Define terms. Highlight why each finding matters.",
    },
}

# ── Heading detection patterns ────────────────────────────────────────────────
_HEADING_PATTERNS: List[re.Pattern] = [
    re.compile(r"^#{1,4}\s+(.+)$",              re.MULTILINE),
    re.compile(r"^(\d+(?:\.\d+)*)\s{1,4}([A-Z][^\n]{2,70})$", re.MULTILINE),
    re.compile(r"^([A-Z][A-Z0-9\s\-:]{4,60}[A-Z0-9])$",       re.MULTILINE),
    re.compile(r"^([A-Z][a-zA-Z0-9\s\-]{3,60})\s*\n[=\-]{4,}", re.MULTILINE),
    re.compile(r"^(?:Section|Chapter|Part|Article)\s+\d+[:\.\s]+(.+)$",
               re.IGNORECASE | re.MULTILINE),
]


class SummarizationSkill(BaseSkill):
    """
    Produces detailed, structured summaries of document text.

    Config keys:
        chunk_size           (int)   : Chars per section chunk (default: 4000)
        chunk_overlap        (int)   : Overlap between chunks   (default: 300)
        max_summary_length   (int)   : max_tokens for LLM      (default: 3000)
        extractive_sentences (int)   : Fallback sentence count  (default: 15)
        -- LLM provider settings are read via LLMClient.from_config() --
    """

    name = "summarization"
    description = "Generates detailed, section-aware summaries via LLM or extractive fallback."
    required_inputs = ["full_text"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._chunk_size      = self.get_config("chunk_size", 4000)
        self._chunk_overlap   = self.get_config("chunk_overlap", 300)
        self._max_tokens      = self.get_config("max_summary_length", 3000)
        self._extractive_n    = self.get_config("extractive_sentences", 15)
        # Max chunks in the map phase — prevents runaway API calls and context overflow
        # on very large documents. Increase in config for full coverage.
        self._max_map_chunks  = self.get_config("max_map_chunks", 20)

        # Build unified LLM client (Grok > Ollama > None)
        from utils.llm_client import LLMClient
        self._llm = LLMClient.from_config(self.config)

    # ── Entry point ───────────────────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start    = time.monotonic()
        full_text: str    = inputs.data["full_text"]
        doc_type: str     = inputs.data.get("doc_type", "normal_document")
        domain: str       = inputs.data.get("domain", "General")
        parsed_doc        = inputs.data.get("parsed_document")

        # Feature 1: summary length
        summary_length = inputs.data.get("summary_length", "Standard")
        length_cfg     = _LENGTH_CONFIGS.get(summary_length, _LENGTH_CONFIGS["Standard"])
        effective_max_tokens  = length_cfg["max_tokens"]
        length_instruction    = length_cfg["instruction"]

        # Feature 2: audience tone
        summary_tone = inputs.data.get("summary_tone", "Professional")
        tone_cfg     = _TONE_PERSONAS.get(summary_tone, _TONE_PERSONAS["Professional"])

        if not full_text.strip():
            return SkillOutput(
                success=False, data=None,
                error="Cannot summarize an empty document.",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        sections      = self._detect_sections(full_text)
        section_names = [s[0] for s in sections]

        skill_warnings: List[str] = []
        if self._llm.available:
            result = self._summarize_llm(
                full_text, doc_type, domain, sections,
                effective_max_tokens, length_instruction, tone_cfg, parsed_doc,
                warnings_out=skill_warnings,
            )
            if result:
                summary, method = result
                self.logger.info(
                    f"Summary via {self._llm.provider_label} ({method}): {len(summary)} chars"
                )
                # Feature 5: parse inline citations from the summary
                citations = self._parse_citations(summary)
                return SkillOutput(
                    success=True,
                    data={"summary": summary, "method": method,
                          "sections_detected": section_names,
                          "provider": self._llm.provider_label,
                          "citations": citations},
                    warnings=skill_warnings,
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            # Name the cause. "failed or timed out" covered a tier refusal and
            # a dead model equally, and those need different responses: one is
            # "ask for less or wait", the other is "your configuration is
            # wrong". This path is how Exhaustive dropped to extractive in
            # silence for months.
            if getattr(self._llm, "_last_failure", None) == \
                    getattr(self._llm, "FAILURE_RATE_LIMIT", "rate_limit_refused"):
                self.logger.warning(
                    f"LLM ({self._llm.provider_label}) REFUSED the request size "
                    f"on every key — the per-minute tier budget could not fit "
                    f"prompt + max_tokens. Falling back to extractive. A shorter "
                    f"summary length would fit; this is not a model failure."
                )
                reason = (f"the {self._llm.provider_label} tier refused this "
                          f"request size (per-minute token limit)")
                skill_warnings.append(
                    "Summary length reduced to extractive: the provider's "
                    "per-minute token limit could not fit this request. "
                    "Try a shorter summary length.")
            else:
                self.logger.warning(
                    f"LLM ({self._llm.provider_label}) failed — falling back to extractive."
                )
                reason = f"LLM provider ({self._llm.provider_label}) failed or timed out"
        else:
            reason = "no LLM provider configured"

        summary = self._extractive_summarize(full_text, sections, reason=reason)

        return SkillOutput(
            success=True,
            data={"summary": summary, "method": "extractive",
                  "sections_detected": section_names, "provider": "none",
                  "citations": []},
            warnings=skill_warnings,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Section detection ─────────────────────────────────────────────────────

    def _detect_sections(self, text: str) -> List[Tuple[str, str]]:
        heading_positions: List[Tuple[int, str]] = []
        for pattern in _HEADING_PATTERNS:
            for m in pattern.finditer(text):
                heading = (m.group(1) if m.lastindex and m.lastindex >= 1
                           else m.group(0)).strip()
                if len(heading) > 3:
                    heading_positions.append((m.start(), heading))

        if not heading_positions:
            return []

        heading_positions.sort(key=lambda x: x[0])
        deduped: List[Tuple[int, str]] = []
        last_end = -1
        for pos, heading in heading_positions:
            if pos > last_end:
                deduped.append((pos, heading))
                last_end = pos + len(heading)

        sections: List[Tuple[str, str]] = []
        for i, (pos, heading) in enumerate(deduped):
            next_pos = deduped[i + 1][0] if i + 1 < len(deduped) else len(text)
            body = text[pos:next_pos].strip()
            body_lines = body.split("\n")
            body = "\n".join(body_lines[1:]).strip() if len(body_lines) > 1 else ""
            if body:
                sections.append((heading, body))

        self.logger.debug(f"Detected {len(sections)} sections")
        return sections

    # ── LLM summarization ─────────────────────────────────────────────────────

    def _summarize_llm(
        self,
        full_text: str,
        doc_type: str,
        domain: str,
        sections: List[Tuple[str, str]],
        max_tokens: int,
        length_instruction: str,
        tone_cfg: Dict[str, str],
        parsed_doc: Any = None,
        warnings_out: Optional[List[str]] = None,
    ) -> Optional[Tuple[str, str]]:
        chunks = self._section_aware_chunks(full_text, sections)
        total_chunks = len(chunks)

        # Cap the map phase to prevent runaway API calls and reduce-prompt overflow.
        if total_chunks > self._max_map_chunks:
            msg = (
                f"Document has {total_chunks} chunks but only the first "
                f"{self._max_map_chunks} were summarized. The summary may not cover the full "
                f"document. Increase 'max_map_chunks' in the summarization config for full coverage."
            )
            self.logger.warning(
                f"Summarization truncated: {total_chunks} chunks → {self._max_map_chunks} "
                f"(set max_map_chunks in config to process more)"
            )
            if warnings_out is not None:
                warnings_out.append(msg)
            chunks = chunks[:self._max_map_chunks]

        self.logger.info(f"Summarizing {len(chunks)} chunk(s) — {self._llm.provider_label} (Domain: {domain})")

        # Build page boundaries for citation tracking
        page_map = self._build_page_boundaries(full_text, parsed_doc)

        if len(chunks) == 1:
            pages = self._pages_for_chunk(chunks[0], full_text, page_map)
            result = self._call_single(
                chunks[0], doc_type, domain, sections,
                max_tokens, length_instruction, tone_cfg, pages,
            )
            return (result, f"llm_single_{self._llm.provider}") if result else None

        chunk_summaries: List[str] = []
        chunk_pages: List[List] = []
        for idx, chunk in enumerate(chunks, 1):
            self.logger.debug(f"  Map chunk {idx}/{len(chunks)}")
            pages = self._pages_for_chunk(chunk, full_text, page_map)
            s = self._call_map(chunk, domain)
            if s:
                chunk_summaries.append(s)
                chunk_pages.append(pages)

        if not chunk_summaries:
            return None

        final = self._call_reduce(
            chunk_summaries, chunk_pages, doc_type, domain, sections,
            max_tokens, length_instruction, tone_cfg,
        )
        return (final, f"llm_map_reduce_{self._llm.provider}") if final else None

    def _call_single(
        self,
        text: str,
        doc_type: str,
        domain: str,
        sections: List[Tuple[str, str]],
        max_tokens: int,
        length_instruction: str,
        tone_cfg: Dict[str, str],
        pages: List,
    ) -> Optional[str]:
        doc_label    = doc_type.replace("_", " ")
        section_hint = ""
        if sections:
            names = ", ".join(f'"{s[0]}"' for s in sections[:8])
            section_hint = f"\nDetected sections: {names}"

        system_content = tone_cfg["system_role"].format(domain=domain)
        if tone_cfg["style"]:
            system_content += f" {tone_cfg['style']}"
        system_content += " Never invent information — only use what is provided."

        # Citation instruction
        page_hint = ""
        if pages:
            page_str = ", ".join(str(p) for p in pages)
            page_hint = (
                f"\nThis content comes from Pages/Sheets: {page_str}. "
                "After each major paragraph or finding, append a source citation in the format "
                "[Source: Page N] or [Source: Pages N, M] based on which part of the document it came from."
            )

        user_content = (
            f"Analyze the following {doc_label} from the perspective of an expert {domain} analyst.\n"
            f"{section_hint}{page_hint}\n\n"
            "IMPORTANT: If this is a dataset (CSV/Excel), ensure you summarize info across ALL categories or groups found "
            "(e.g., all cities, all departments), identifying overall trends and outliers.\n\n"
            "Begin with a single-line title (using `#`) that captures what this specific document is actually about — "
            "do not use a generic title like 'Document Summary' or 'Analysis Report'.\n\n"
            "Then write the analysis using Markdown section headings that emerge naturally from the content itself. "
            "Choose headings based on the topics, themes, and structure found in the document — "
            "do not apply a fixed template.\n\n"
            f"Document:\n\n{text}\n\n"
            "Analysis:"
        )
        if length_instruction:
            user_content += f"\n\nLength/depth directive: {length_instruction}"

        return self._llm.chat(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user",   "content": user_content},
            ],
            # `max_tokens` here is the preset's CONTENT budget; the reasoning
            # allowance is added so the preset delivers the length it names.
            max_tokens=_with_reasoning_room(max_tokens),
        )

    def _call_map(self, chunk: str, domain: str) -> Optional[str]:
        """Extract facts from a single chunk. Tone-neutral — purely factual extraction."""
        return self._llm.chat(
            messages=[{
                "role": "user",
                "content": (
                    f"You are an expert {domain} Analyst extracting data from a document section.\n"
                    "Extract the most critical metrics, data points, trends, and facts relevant to your domain.\n"
                    "Include: specific facts/figures/dates, important entities, outliers, or decisions.\n"
                    "Be specific — do not paraphrase vaguely. Preserve numbers and proper nouns.\n\n"
                    f"Section:\n{chunk}\n\n"
                    "Extracted Facts (bullet list):"
                ),
            }],
            temperature=0.1,
            # The failure mode is why this needs room: this method's caller does
            # `if s: chunk_summaries.append(s)`, so a truncated chunk is quietly
            # dropped or half-kept and the summary loses a section without
            # erroring. Measured need is ~943 content tokens on the largest real
            # chunks; 800 was truncating in practice.
            max_tokens=_with_reasoning_room(_MAP_CONTENT_TOKENS),
        )

    def _call_reduce(
        self,
        chunk_summaries: List[str],
        chunk_pages: List[List],
        doc_type: str,
        domain: str,
        sections: List[Tuple[str, str]],
        max_tokens: int,
        length_instruction: str,
        tone_cfg: Dict[str, str],
    ) -> Optional[str]:
        doc_label = doc_type.replace("_", " ")

        system_content = tone_cfg["system_role"].format(domain=domain)
        if tone_cfg["style"]:
            system_content += f" {tone_cfg['style']}"
        system_content += " Synthesize extracted data into a production-grade, domain-specific analysis report. Never invent information — only use what is provided."

        # Build page-tagged fact blocks for citations
        tagged_blocks: List[str] = []
        for bullets, pages in zip(chunk_summaries, chunk_pages):
            if pages:
                page_str = ", ".join(str(p) for p in pages)
                tagged_blocks.append(f"[Source: Pages {page_str}]\n{bullets}")
            else:
                tagged_blocks.append(bullets)
        combined = "\n\n---\n\n".join(tagged_blocks)

        user_content = (
            f"Below are key facts extracted from sections of a {doc_label}. "
            "Each section is tagged with [Source: Pages ...] indicating which pages it came from.\n\n"
            f"Synthesize them into a highly professional {domain} analysis. "
            "Cover the BREADTH of information — if different chunks focused on different groups "
            "(e.g., cities, dates, entities), include all of them to highlight overall trends and comparisons.\n\n"
            "Begin with a single-line title (using `#`) that captures what this specific document is actually about — "
            "do not use a generic title like 'Document Summary' or 'Analysis Report'.\n\n"
            "Then organize the analysis using Markdown section headings that reflect the actual topics and themes "
            "found in the content — do not follow a fixed template. Let the structure emerge from what the document covers.\n\n"
            "CITATIONS: When making a claim derived from a specific section, append the matching source tag inline, "
            "like: 'Revenue grew 12% [Source: Pages 3, 4]'. Preserve source tags from the input in your output.\n\n"
            f"Extracted facts:\n\n{combined}\n\n"
            "Analysis:"
        )
        if length_instruction:
            user_content += f"\n\nLength/depth directive: {length_instruction}"

        return self._llm.chat(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user",   "content": user_content},
            ],
            # `max_tokens` here is the preset's CONTENT budget; the reasoning
            # allowance is added so the preset delivers the length it names.
            max_tokens=_with_reasoning_room(max_tokens),
        )

    # ── Page-to-chunk mapping (for citations) ─────────────────────────────────

    @staticmethod
    def _build_page_boundaries(full_text: str, parsed_doc: Any) -> List[Tuple[int, int, Any]]:
        """Return (start_char, end_char, page_or_sheet) for each parsed chunk."""
        if parsed_doc is None or not hasattr(parsed_doc, "chunks"):
            return []
        boundaries: List[Tuple[int, int, Any]] = []
        pos = 0
        for chunk in parsed_doc.chunks:
            end = pos + len(chunk.text)
            boundaries.append((pos, end, chunk.page_or_sheet))
            pos = end + 2  # account for "\n\n" separator used in full_text join
        return boundaries

    @staticmethod
    def _pages_for_chunk(chunk_text: str, full_text: str, page_map: List[Tuple[int, int, Any]]) -> List:
        """Find which page_or_sheet values overlap with a text chunk's position in full_text."""
        if not page_map or not chunk_text:
            return []
        probe = chunk_text[:50]
        start = full_text.find(probe)
        if start == -1:
            return []
        end = start + len(chunk_text)
        pages: List = []
        for b_start, b_end, page in page_map:
            if b_start < end and b_end > start and page not in pages:
                pages.append(page)
        return pages

    @staticmethod
    def _parse_citations(summary: str) -> List[Dict[str, Any]]:
        """Extract inline [Source: Page N] / [Source: Pages N, M] tags from the summary."""
        citation_pattern = re.compile(r'\[Source: Pages? ([^\]]+)\]')
        citations: List[Dict[str, Any]] = []
        for i, para in enumerate(summary.split("\n\n")):
            matches = citation_pattern.findall(para)
            pages: List = []
            for match in matches:
                for part in match.split(","):
                    p = part.strip()
                    try:
                        pages.append(int(p))
                    except ValueError:
                        if p:
                            pages.append(p)
            if pages:
                citations.append({
                    "paragraph_index": i,
                    "pages": pages,
                    "section": para[:60].replace("\n", " "),
                })
        return citations

    # ── Section-aware chunking ─────────────────────────────────────────────────

    def _section_aware_chunks(
        self,
        full_text: str,
        sections: List[Tuple[str, str]],
    ) -> List[str]:
        if not sections:
            return self._char_chunks(full_text)

        chunks: List[str] = []
        current = ""
        for heading, body in sections:
            section_text = f"## {heading}\n\n{body}"
            if len(current) + len(section_text) > self._chunk_size and current:
                chunks.append(current.strip())
                current = section_text
            else:
                current = (current + "\n\n" + section_text).strip()
        if current:
            chunks.append(current.strip())

        result: List[str] = []
        for chunk in chunks:
            if len(chunk) > self._chunk_size * 1.5:
                result.extend(self._char_chunks(chunk))
            else:
                result.append(chunk)
        return result if result else [full_text]

    def _char_chunks(self, text: str) -> List[str]:
        if len(text) <= self._chunk_size:
            return [text]

        chunks: List[str] = []
        start, text_len = 0, len(text)
        BREAKS = ["\n\n", ".\n", ". ", "? ", "! ", "; "]

        while start < text_len:
            end = min(start + self._chunk_size, text_len)
            if end >= text_len:
                chunks.append(text[start:])
                break
            break_at = end
            for token in BREAKS:
                idx = text.rfind(token, start + self._chunk_overlap, end)
                if idx != -1:
                    break_at = idx + len(token)
                    break
            chunks.append(text[start:break_at])
            start = max(start + 1, break_at - self._chunk_overlap)
        return chunks

    # ── Extractive fallback ────────────────────────────────────────────────────

    def _extractive_summarize(
        self, text: str, sections: List[Tuple[str, str]], reason: str = "unknown"
    ) -> str:
        header = f"[Note: Generated by extractive summarization — {reason}]\n\n"
        if sections:

            return header + self._extractive_with_sections(text, sections)
        return header + self._extractive_flat(text)

    def _extractive_with_sections(
        self, _full_text: str, sections: List[Tuple[str, str]]
    ) -> str:
        per_section = max(2, self._extractive_n // max(len(sections), 1))
        parts: List[str] = []
        for heading, body in sections:
            top = self._top_sentences(body, n=per_section)
            if top:
                parts.append(f"**{heading}**\n" + " ".join(top))
        return "\n\n".join(parts)[: 4000] if parts else self._extractive_flat(_full_text)

    def _extractive_flat(self, text: str) -> str:
        top = self._top_sentences(text, n=self._extractive_n)
        return (" ".join(top) if top else text[:500])[:4000]

    def _top_sentences(self, text: str, n: int) -> List[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 25]
        if not sentences:
            return []
        if len(sentences) <= n:
            return sentences

        all_words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        freq = Counter(w for w in all_words if w not in _STOPWORDS)
        total = len(sentences)

        def score(sent: str, idx: int) -> float:
            ws = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", sent.lower())
                  if w not in _STOPWORDS]
            tfidf     = sum(freq.get(w, 0) for w in ws) / (len(ws) + 1)
            pos_ratio = idx / max(total, 1)
            pos_bonus = 1.2 if pos_ratio < 0.1 or pos_ratio > 0.9 else 1.0
            len_bonus = 1.1 if 40 < len(sent) < 200 else 0.9
            return tfidf * pos_bonus * len_bonus

        scored = [(score(s, i), i, s) for i, s in enumerate(sentences)]
        scored.sort(reverse=True)
        top = sorted(scored[:n], key=lambda x: x[1])
        return [s for _, _, s in top]
