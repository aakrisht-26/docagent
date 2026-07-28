"""
FormFillingSkill — an LLM contextual compiler that rewrites raw OCR text matching the form layout but filling in blanks with User Answers.
"""

from __future__ import annotations

import time
import json
from typing import Any, Dict, List, Optional

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.llm_client import LLMClient
from utils.logger import get_logger

logger = get_logger(__name__)


class FormFillingSkill(BaseSkill):
    """
    Acts as an Intelligent Document Compiler.
    Restructures the document inserting the user's answers exactly after their corresponding questions.
    """

    name = "form_filling"
    description = "Restructures a document to include user-provided answers while preserving original context."
    required_inputs = ["raw_text", "questions", "user_answers", "model"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._llm = LLMClient.from_config(self.config)

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()
        raw_text = inputs.data["raw_text"]
        questions = inputs.data["questions"]
        user_answers = inputs.data["user_answers"]  # Dict[question, answer]
        model = inputs.data.get("model", "llama-3.3-70b-versatile")
        
        # Guard clause
        if not raw_text or not questions or not user_answers:
            return SkillOutput(success=False, data=None, error="Missing required data for Form Filling")

        self.logger.info(f"FormFilling: Restructuring document with {len(user_answers)} answers...")

        # Constructing the JSON injection string
        answers_json = json.dumps(user_answers, indent=2)

        prompt = f"""
You are an Intelligent Document Compiler. 
Your task is to take the raw extracted OCR text of a form/questionnaire and output a beautifully structured Markdown document.

CRITICAL INSTRUCTIONS:
1. Preserve the visual layout, structural headings, introductory text, and context of the input file exactly as much as possible. Do NOT overwrite existing content or delete instructions.
2. The user has provided a JSON dictionary mapping exact Questions -> User's Answer.
3. Your job is to rewrite the raw_text, but whenever you encounter one of the listed questions, you MUST insert the User's Answer immediately after that question.
4. If there are any blanks (e.g. "Name: _______"), remove them to make space for the answer.
5. NEVER modify the phrasing or words of the original questions. They must remain exactly as they appear in the original text.
6. NEVER modify the wording or content of the User's Answer. Insert it exactly as provided.
7. Format the User's Answer by placing it on a NEW LINE directly below the question. Prefix it with "**Answer:** " and format the answer text in bold. (e.g., \n**Answer:** **[User's Answer]**).
8. Return ONLY the finalized Markdown text. Do not return any XML blocks or conversational intro.

=== ORIGINAL RAW TEXT ===
{raw_text}

=== USER ANSWERS JSON ===
{answers_json}

=== FINAL MARKDOWN (Include NO other output) ===
"""

        try:
            messages = [{"role": "user", "content": prompt}]
            filled_text = self._llm.chat(
                messages=messages,
                temperature=0.2, # Low temperature to prevent hallucinating changes to the questions
                max_tokens=6000
            )
            
            if not filled_text:
                raise ValueError("LLM returned empty response")
            
            # Clean up potential markdown fences thrown by the model
            if filled_text.startswith("```markdown"):
                filled_text = filled_text[11:]
            if filled_text.endswith("```"):
                filled_text = filled_text[:-3]
                
            filled_text = filled_text.strip()
            
            return SkillOutput(
                success=True,
                data={"filled_markdown": filled_text},
                duration_ms=(time.monotonic() - start) * 1000
            )

        except Exception as e:
            self.logger.error(f"Form Filling LLM failed: {e}")
            return SkillOutput(success=False, data=None, error=str(e), duration_ms=(time.monotonic() - start) * 1000)
