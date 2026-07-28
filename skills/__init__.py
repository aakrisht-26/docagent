"""
DocAgent skills package.

Each skill is an atomic, reusable capability.
Skills are auto-discovered by SkillRegistry at startup.

Skills:
- base_skill.py                : Abstract base interface
- pdf_reader_skill.py          : Parse PDF documents
- excel_reader_skill.py        : Parse Excel / CSV files
- text_cleaner_skill.py        : Normalize and chunk text
- document_classifier_skill.py : Detect questionnaire vs normal document
- summarization_skill.py       : Generate structured summaries
- question_extraction_skill.py : Extract questions from forms
"""
