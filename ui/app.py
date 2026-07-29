"""
Main Streamlit application entry point for DocAgent.

Run with:
    streamlit run ui/app.py
"""

from __future__ import annotations

import sys
import time
import os
import traceback
from pathlib import Path

# ── Path fixup so app can be run from repo root ────────────────────────────────
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

# ── Page config (must be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="DocAgent",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from utils.config import load_config
from utils.logger import setup_logging, get_logger
from utils.file_handler import save_upload, cleanup_temp_dir, make_temp_dir, validate_file, is_valid_youtube_url
from utils.document_store import DocumentStore

# Load config & initialise logging once
_cfg = load_config()
setup_logging(level=_cfg.log_level, log_file=_cfg.log_file)
logger = get_logger("ui.app")

# Singleton document store (persists to ~/.docagent/history.db)
_store = DocumentStore()


# ── CSS injection ──────────────────────────────────────────────────────────────

_LIGHT_VARS = """
<style>
:root {
  --bg-base:        #fafafa;
  --bg-surface:     #ffffff;
  --bg-card:        #ffffff;
  --border:         #e4e7ec;
  --border-accent:  rgba(29,78,216,0.15);
  --text-primary:   #0f172a;
  --text-secondary: #475569;
  --text-muted:     #94a3b8;
  --accent:         #1D4ED8;
  --accent-light:   #2563EB;
  --accent-2:       #0891b2;
  --accent-2-light: #0891b2;
  --success:        #059669;
  --warning:        #d97706;
  --error:          #dc2626;
  --sidebar-bg:     #f4f4f7;
}

/* ── App shell ── */
[data-testid="stAppViewContainer"]  { background: #fafafa !important; }
[data-testid="stHeader"]            { background: #fafafa !important; border-bottom: 1px solid #e4e7ec !important; }
[data-testid="stSidebar"]           { background: #f4f4f7 !important; border-right: 1px solid #e4e7ec !important; }
[data-testid="stSidebar"] > div     { background: #f4f4f7 !important; }

/* ── All native Streamlit text in light mode ── */
p, span, label, h1, h2, h3, h4, h5, li,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] span,
[data-testid="stText"],
.stRadio label, .stSelectbox label, .stCheckbox label,
[data-testid="stCaptionContainer"] p,
[data-baseweb="radio"] label,
[data-baseweb="select"] span,
[class*="st-emotion"] p,
[class*="st-emotion"] span { color: #0f172a !important; }

/* ── Input / select / textarea backgrounds ── */
[data-baseweb="select"] > div,
[data-baseweb="input"] > div,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child,
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
  background: #ffffff !important;
  border-color: #d1d5db !important;
  color: #0f172a !important;
}

/* ── Regular action buttons in light mode (not download buttons) ── */
[data-testid="stBaseButton-secondary"]:not([data-testid="stDownloadButton"] button) {
  background: rgba(29,78,216,0.07) !important;
  color: #1D4ED8 !important;
  border: 1px solid rgba(29,78,216,0.2) !important;
}

/* ── Theme toggle stays round in light mode too ── */
.theme-toggle-wrap > div > div > button,
.theme-toggle-wrap [data-testid="stBaseButton-secondary"] {
  background: rgba(29,78,216,0.08) !important;
  border: 1px solid rgba(29,78,216,0.2) !important;
  color: #1D4ED8 !important;
}
.theme-toggle-wrap > div > div > button:hover,
.theme-toggle-wrap [data-testid="stBaseButton-secondary"]:hover {
  background: rgba(29,78,216,0.16) !important;
  border-color: rgba(29,78,216,0.4) !important;
}

/* ── Download buttons keep their gradients in light mode ── */
.btn-pdf [data-testid="stDownloadButton"] button {
  background: linear-gradient(135deg, #1D4ED8, #2563EB) !important;
  color: white !important;
}
.btn-md [data-testid="stDownloadButton"] button {
  background: linear-gradient(135deg, #0891b2, #06B6D4) !important;
  color: white !important;
}
.btn-json [data-testid="stDownloadButton"] button {
  background: linear-gradient(135deg, #065f46, #059669) !important;
  color: white !important;
}

/* ── Selectbox dropdown popup ── */
[data-baseweb="popover"],
[data-baseweb="menu"],
[role="listbox"],
ul[data-baseweb="menu"] {
  background: #ffffff !important;
  border: 1px solid #e4e7ec !important;
  box-shadow: 0 4px 16px rgba(0,0,0,0.08) !important;
}
[data-baseweb="option"],
li[role="option"],
[role="option"] {
  background: #ffffff !important;
  color: #0f172a !important;
}
[data-baseweb="option"]:hover,
li[role="option"]:hover,
[role="option"]:hover,
[aria-selected="true"][role="option"] {
  background: rgba(29,78,216,0.08) !important;
  color: #0f172a !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] > div:first-child {
  background: rgba(29,78,216,0.025) !important;
  border-color: rgba(29,78,216,0.3) !important;
}
[data-testid="stFileUploaderDropzone"],
[data-testid="stFileUploaderDropzone"] > div,
[data-testid="stFileUploaderDropzoneInstructions"] {
  background: #ffffff !important;
  color: #0f172a !important;
}
[data-testid="stFileUploaderDropzone"] button {
  background: #1D4ED8 !important;
  color: #ffffff !important;
}

/* ── Dividers and borders ── */
hr { border-color: #e4e7ec !important; }

/* ── Code blocks inside chat bubbles and markdown ── */
pre,
[data-testid="stMarkdownContainer"] pre,
.chat-bubble-bot pre,
.chat-bubble-user pre {
  background: #f1f5f9 !important;
  border: 1px solid #e4e7ec !important;
  border-radius: 8px !important;
}
pre code,
[data-testid="stMarkdownContainer"] pre code,
.chat-bubble-bot pre code,
.chat-bubble-user pre code {
  background: transparent !important;
  color: #1e293b !important;
}
/* Streamlit's st.code / stCodeBlock container */
[data-testid="stCodeBlock"],
[data-testid="stCodeBlock"] > div,
[data-testid="stCodeBlock"] pre {
  background: #f1f5f9 !important;
  border: 1px solid #e4e7ec !important;
  border-radius: 8px !important;
  color: #1e293b !important;
}
[data-testid="stCodeBlock"] code,
[data-testid="stCodeBlock"] span {
  color: #1e293b !important;
}
/* Inline code */
code:not(pre code) {
  background: #e8edf5 !important;
  color: #1D4ED8 !important;
  border-radius: 4px !important;
  padding: 1px 5px !important;
}

/* ── Custom component overrides ── */
.doc-type-banner.questionnaire  { color: #0891b2; background: rgba(8,145,178,0.07); border-color: rgba(8,145,178,0.2); }
.doc-type-banner.normal         { color: #1D4ED8; background: rgba(29,78,216,0.06); border-color: rgba(29,78,216,0.15); }
.stat-value                     { color: #1D4ED8; }
.stat-label                     { color: #94a3b8; }
.stat-card                      { background: #ffffff; border-color: #e4e7ec; }
.summary-section-heading        { color: #1D4ED8; border-color: rgba(29,78,216,0.2); }
.summary-subsection-heading     { color: #0891b2; }
.summary-para                   { color: #475569; }
.summary-bullet                 { color: #475569; border-left-color: rgba(29,78,216,0.25); }
.chat-bubble-user               { background: rgba(29,78,216,0.08); border-color: rgba(29,78,216,0.18); color: #0f172a; }
.chat-bubble-bot                { background: #ffffff; border-color: #e4e7ec; color: #0f172a; }
.question-item                  { background: #ffffff; border-color: #e4e7ec; }
.question-text                  { color: #0f172a; }
.question-num                   { background: rgba(29,78,216,0.1); color: #1D4ED8; }
.confidence-meter-bg            { background: #e4e7ec; }
.hero-subtitle                  { color: #64748b; }
.badge-blue, .badge-purple      { background: rgba(29,78,216,0.1); color: #1D4ED8; border-color: rgba(29,78,216,0.25); }
.meta-table td:first-child      { color: #1D4ED8; }
.options-bar [data-testid="stWidgetLabel"] p { color: #94a3b8 !important; }
.timing-bar-fill                { background: linear-gradient(90deg, #1D4ED8, #0891b2); }
</style>
"""


def _inject_css() -> None:
    css_path = Path(__file__).parent / "styles" / "custom.css"
    if css_path.exists():
        with open(css_path, encoding="utf-8") as fh:
            css = fh.read()
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    # Inject theme variable overrides on top of base CSS
    if st.session_state.get("theme") == "light":
        st.markdown(_LIGHT_VARS, unsafe_allow_html=True)


_inject_css()


# ── Sidebar ────────────────────────────────────────────────────────────────────

def _render_sidebar() -> None:
    """Sidebar with theme toggle and config health warnings."""
    with st.sidebar:
        # ── Header row: title + theme icon toggle ──────────────────────
        hdr_col, toggle_col = st.columns([3, 1])
        with hdr_col:
            st.markdown("## DocAgent")
        with toggle_col:
            theme = st.session_state.get("theme", "dark")
            icon  = "☀️" if theme == "dark" else "🌙"
            st.markdown('<div class="theme-toggle-wrap">', unsafe_allow_html=True)
            if st.button(icon, key="theme_toggle",
                         help="Switch to light mode" if theme == "dark" else "Switch to dark mode"):
                st.session_state.theme = "light" if theme == "dark" else "dark"
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        st.divider()

        # ── Config health check ────────────────────────────────────────
        cfg_issues = _cfg.validate()
        if cfg_issues:
            st.divider()
            st.markdown("**⚠️ Configuration issues**")
            for issue in cfg_issues:
                st.warning(issue, icon="⚙️")

        # ── Recent documents ───────────────────────────────────────────
        st.divider()
        st.markdown("**Recent Documents**")
        try:
            recent = _store.list_recent(limit=10)
            if not recent:
                st.caption("No documents processed yet.")
            else:
                for entry in recent:
                    label = f"📄 {entry.file_name[:28]}"
                    subtext = f"{entry.domain} · {entry.word_count:,} words"
                    if st.button(label, key=f"hist_{entry.entry_id}",
                                 help=subtext, use_container_width=True):
                        cached = _store.load(entry.entry_id)
                        if cached:
                            st.session_state["loaded_from_history"] = cached
                            st.rerun()
                if recent:
                    if st.button("🗑 Clear history", use_container_width=True,
                                 key="clear_history"):
                        _store.clear_all()
                        st.rerun()
        except Exception as exc:
            st.caption(f"History unavailable: {exc}")


# ── Agent factory ──────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_agent(summary_length: str = "Standard", summary_tone: str = "Professional"):
    """Build (and cache) a DocumentAgent with the given settings."""
    from agents.document_agent import DocumentAgent
    from utils.config import load_config

    cfg = load_config()
    agent_cfg = cfg.to_dict()
    agent_cfg["summarization"]["summary_length"] = summary_length
    agent_cfg["summarization"]["summary_tone"]   = summary_tone

    return DocumentAgent(config=agent_cfg)


# ── Hero header ────────────────────────────────────────────────────────────────

def _render_header() -> None:
    st.markdown("""
    <div class="hero-header fade-in">
      <h1 class="hero-title">DocAgent</h1>
      <p class="hero-subtitle">Intelligent document analysis</p>
    </div>
    """, unsafe_allow_html=True)


# ── Upload + options zone ──────────────────────────────────────────────────────

def _render_upload() -> tuple[list, dict]:
    """Render file uploader, YouTube input, and inline summary options. Returns (files, overrides)."""
    col = st.columns([1, 3, 1])[1]
    with col:
        # ── Input mode selector ────────────────────────────────────────
        st.markdown("**Upload or analyze:**")
        input_mode = st.radio(
            "Choose input method",
            options=["Upload Files", "YouTube URL"],
            horizontal=True,
            label_visibility="collapsed",
            key="input_mode_radio",
        )

        files = []
        youtube_url = None

        if input_mode == "Upload Files":
            files = st.file_uploader(
                "Drop files here or click to browse",
                type=["pdf", "xlsx", "xls", "csv", "mp3", "m4a", "wav", "flac", "ogg", "webm"],
                accept_multiple_files=True,
                label_visibility="visible",
                help="PDF, Excel (.xlsx / .xls), CSV, or Audio (MP3, WAV, M4A, FLAC, OGG, WebM) · max 50 MB per file",
            )
        else:
            youtube_url = st.text_input(
                "YouTube URL",
                placeholder="https://www.youtube.com/watch?v=...",
                help="Paste a YouTube video link. Audio will be extracted and transcribed.",
                label_visibility="visible",
            )

        # ── Inline options bar ─────────────────────────────────────────
        st.markdown('<div class="options-bar">', unsafe_allow_html=True)
        opt_cols = st.columns([2, 2])
        with opt_cols[0]:
            summary_length = st.radio(
                "Summary length",
                options=["Concise", "Standard", "Detailed", "Exhaustive"],
                index=1,
                horizontal=True,
                label_visibility="visible",
                key="summary_length_radio",
            )
        with opt_cols[1]:
            summary_tone = st.selectbox(
                "Audience",
                options=["Expert / Technical", "Professional", "General / Non-technical", "Student"],
                index=1,
                label_visibility="visible",
            )
        st.markdown('</div>', unsafe_allow_html=True)

    overrides = {"summary_length": summary_length, "summary_tone": summary_tone}

    # Convert YouTube URL to stable file entry (name must survive reruns)
    if input_mode == "YouTube URL" and youtube_url:
        url_stripped = youtube_url.strip()
        if url_stripped:
            # Use video ID (or a short hash) as stable key so theme-toggle reruns
            # don't generate a new name and invalidate the cached result.
            from utils.file_handler import extract_youtube_video_id
            video_id = extract_youtube_video_id(url_stripped) or str(abs(hash(url_stripped)) % 10**8)
            files = [{"youtube_url": url_stripped, "name": f"youtube_{video_id}.audio"}]
        else:
            files = []
    else:
        files = files or []

    return files, overrides


# ── Progress display ───────────────────────────────────────────────────────────

STEP_LABELS = {
    "parse":                 "Parsing document…",
    "clean":                 "Normalising text…",
    "classify":              "Classifying document…",
    "structure_recognition": "Recognising structure…",
    "summarize":             "Generating summary…",
    "extract_questions":     "Extracting questions…",
    "done":                  "Analysis complete",
}


def _run_pipeline(name: str, file_data: dict, overrides: dict) -> None:
    """Run the document pipeline for a single file or YouTube URL."""
    from ui.components.results_view import render_results

    # Cache key is by filename — survives theme-toggle reruns
    state_key = f"doc_result_{name}"
    if state_key in st.session_state:
        render_results(st.session_state[state_key], export_cfg=_cfg.export)
        return

    tmp_dir = make_temp_dir()
    try:
        # Determine input type: YouTube URL or file
        youtube_url = file_data.get("youtube_url")
        is_youtube = youtube_url is not None

        if is_youtube:
            # Validate YouTube URL
            if not is_valid_youtube_url(youtube_url):
                st.error(f"Invalid YouTube URL: {youtube_url}")
                return
            file_path = Path(tmp_dir) / name
            file_path.touch()  # Create a placeholder file for path-based processing
        else:
            # Regular file upload
            file_bytes = file_data.get("bytes", b"")
            file_path = save_upload(file_bytes, name, tmp_dir)
            err = validate_file(file_path, max_size_mb=_cfg.max_file_size_mb)
            if err:
                st.error(err)
                return

        agent = _get_agent(
            summary_length=overrides.get("summary_length", "Standard"),
            summary_tone=overrides.get("summary_tone", "Professional"),
        )

        progress_placeholder = st.empty()
        status_placeholder   = st.empty()
        total = len(STEP_LABELS)

        with progress_placeholder.container():
            bar = st.progress(0, text="Starting…")

        start_ts = time.monotonic()

        # Walk back to the original _log_step regardless of how many times the
        # cached agent has been patched in previous runs.
        _orig_log = agent._log_step
        while hasattr(_orig_log, "__wrapped_orig__"):
            _orig_log = _orig_log.__wrapped_orig__  # type: ignore[attr-defined]

        step_counter = [0]

        def _progress_log_step(skill_name, success, duration_ms, error=None):
            _orig_log(skill_name, success, duration_ms, error)
            step_counter[0] += 1
            pct = min(step_counter[0] / total, 0.95)
            label = STEP_LABELS.get(skill_name, skill_name.replace("_", " ").title() + "…")
            bar.progress(pct, text=label)

        _progress_log_step.__wrapped_orig__ = _orig_log  # type: ignore[attr-defined]
        agent._log_step = _progress_log_step  # type: ignore[method-assign]

        try:
            # Run pipeline with YouTube URL or file path
            if is_youtube:
                result = agent.run_youtube(youtube_url)
            else:
                result = agent.run(file_path)
        finally:
            # Always restore the original method so the cached agent stays clean
            agent._log_step = _orig_log  # type: ignore[method-assign]

        bar.progress(1.0, text="Complete")
        elapsed = time.monotonic() - start_ts

        with status_placeholder.container():
            if result.success:
                st.session_state[state_key] = result
                st.session_state[f"parsed_doc_{name}"] = result.parsed_document
                # Auto-save to persistent history (non-blocking; errors are warnings)
                try:
                    raw_bytes = file_data.get("bytes")
                    _store.save(result, raw_bytes=raw_bytes)
                except Exception as store_exc:
                    logger.warning(f"Could not save result to history: {store_exc}")
                st.success(
                    f"Analysis complete in **{elapsed:.1f}s** — "
                    f"classified as **{result.doc_type.replace('_', ' ').title()}**"
                )
            else:
                st.error(f"Pipeline finished with errors: {', '.join(result.errors)}")

        render_results(result, export_cfg=_cfg.export)

    except Exception as exc:
        # Log the full traceback before doing anything else, so the failure is
        # recoverable from logs/docagent.log even if the UI render below fails.
        logger.exception("Pipeline error while processing %r", name)

        # In debug mode, don't absorb the exception — let it propagate so
        # Streamlit shows its own traceback and debuggers can break on it.
        # Enable with DOCAGENT_DEBUG=true, or app.debug in configs/default.yaml.
        if _cfg.debug:
            raise

        # Surface the exception TYPE as well as the message. A bare str(exc) is
        # frequently empty (e.g. KeyError('x') renders as "'x'", IndexError as
        # ""), which produced a "Processing failed:" message with nothing after
        # it and no way to tell what went wrong.
        detail = str(exc).strip() or "(no message)"
        st.error(f"Processing failed — {type(exc).__name__}: {detail}")
        st.caption(
            "Full traceback written to the log. Set `DOCAGENT_DEBUG=true` to "
            "re-raise instead of catching."
        )
        with st.expander("Show traceback"):
            st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    language="text")
    finally:
        cleanup_temp_dir(tmp_dir)


# ── History helper ─────────────────────────────────────────────────────────────

def _dict_to_pipeline_result(d: dict):
    """Reconstruct a displayable PipelineResult from a stored dict (no raw_text or parsed_document)."""
    from core.pipeline_result import PipelineResult
    return PipelineResult(
        file_name=d.get("file_name", "unknown"),
        file_type=d.get("file_type", "unknown"),
        doc_type=d.get("doc_type", "normal_document"),
        domain=d.get("domain", "General"),
        classification_confidence=d.get("classification_confidence", 0.0),
        classification_method=d.get("classification_method", "unknown"),
        summary=d.get("summary", ""),
        summary_method=d.get("summary_method", "unknown"),
        questions=d.get("questions", []),
        question_extraction_method=d.get("question_extraction_method", "unknown"),
        raw_text="",  # Not persisted — chat/edit unavailable for history results
        word_count=d.get("word_count", 0),
        page_count=d.get("page_count", 0),
        metadata=d.get("metadata", {}),
        extracted_entities=d.get("extracted_entities", {}),
        errors=d.get("errors", []),
        warnings=d.get("warnings", []),
        processing_time_ms=d.get("processing_time_ms", 0.0),
        skill_timings=d.get("skill_timings", {}),
        success=d.get("success", True),
        partial=d.get("partial", False),
    )


# ── Main app ───────────────────────────────────────────────────────────────────

def main() -> None:
    _render_sidebar()
    _render_header()

    # ── Restore a result loaded from the history sidebar ───────────────
    if "loaded_from_history" in st.session_state:
        cached_dict = st.session_state.pop("loaded_from_history")
        result = _dict_to_pipeline_result(cached_dict)
        file_name = result.file_name
        # Store under the normal state key so the render loop picks it up.
        st.session_state[f"doc_result_{file_name}"] = result
        # Set _file_data so the file appears as "processed" in the render loop.
        # (No "bytes" needed — _run_pipeline exits early via the state-key check.)
        st.session_state["_file_data"] = [{"name": file_name}]

    uploaded_files, overrides = _render_upload()

    # ── Persist file data immediately so theme toggle can't lose them ──
    # Also clear stale data when the uploader is explicitly emptied.
    if uploaded_files:
        # Handle both Streamlit UploadedFile objects and dict entries (for YouTube)
        file_data_list = []
        for item in uploaded_files:
            if isinstance(item, dict):
                # YouTube URL entry
                file_data_list.append(item)
            else:
                # Streamlit UploadedFile
                file_data_list.append({"name": item.name, "bytes": item.getvalue()})
        st.session_state["_file_data"] = file_data_list
    elif st.session_state.get("_input_mode") == "Upload Files":
        # Uploader is in file mode but returned nothing — user cleared it.
        # Wipe stale _file_data so old results don't linger.
        st.session_state.pop("_file_data", None)

    # Track current input mode so we can detect when the uploader is cleared.
    st.session_state["_input_mode"] = st.session_state.get("input_mode_radio", "Upload Files")

    file_data: list[dict] = st.session_state.get("_file_data", [])

    if not file_data:
        st.markdown("""
        <div class="empty-state fade-in" style="padding: 4rem 1rem">
          <h3>Upload a document or paste a YouTube link to get started</h3>
          <p>Supports PDF, Excel (.xlsx / .xls), CSV, Audio (MP3, WAV, etc.), and YouTube videos</p>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Separate already-processed from pending files ──────────────────
    pending   = [fd for fd in file_data if f"doc_result_{fd['name']}" not in st.session_state]
    processed = [fd for fd in file_data if f"doc_result_{fd['name']}" in st.session_state]

    # ── Show results for already-processed files first ─────────────────
    # (so the Analyze button for new files appears at the bottom, where
    #  the user is looking after scrolling through previous results)
    for fd in processed:
        st.markdown(f"---\n### `{fd['name']}`")
        _run_pipeline(fd["name"], fd, overrides)

    # ── Show Analyze button for pending files at the bottom ────────────
    if pending:
        if processed:
            st.divider()
        names_str = ", ".join(f"`{fd['name']}`" for fd in pending)
        st.caption(f"Ready to analyse: {names_str}")
        btn_col = st.columns([1, 2, 1])[1]
        # Capture click inside the column (for centering) but run the
        # pipeline OUTSIDE it so render_results always has full width.
        with btn_col:
            do_analyze = st.button("Analyze", type="primary", use_container_width=True, key="run_btn")
        if do_analyze:
            for fd in pending:
                st.markdown(f"---\n### `{fd['name']}`")
                _run_pipeline(fd["name"], fd, overrides)


if __name__ == "__main__":
    main()
