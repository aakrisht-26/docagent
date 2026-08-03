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

_LIGHT_TOKENS = """
<style>
/* Light theme = the same design-token ROLES from custom.css, re-valued.

   This block used to be 189 lines: 16 token values plus 51 `!important`
   overrides across 42 Streamlit-internal selectors. Because every light
   surface had to be listed by hand, anything nobody remembered stayed dark
   — sidebar history buttons rendered dark-on-dark, download buttons were
   black-on-black, unselected radios showed as filled dots, and the page
   background below the fold never switched.

   Now it swaps tokens only. The element rules live once in custom.css,
   written against var(--token), so they are correct in both themes by
   construction and a new surface cannot be forgotten in one of them.

   Dark values live in custom.css :root. Light values live HERE. One place
   per theme — do not mirror these into custom.css or they will drift. */
:root {
  --bg-base:        #ffffff;
  --bg-surface:     #f6f7f9;
  --bg-card:        #ffffff;
  --bg-input:       #ffffff;
  --bg-hover:       #eef1f6;

  --border:         #e2e6ed;
  --border-strong:  #cbd3e0;
  --border-accent:  rgba(29,78,216,0.35);

  --text-primary:   #0f172a;
  --text-secondary: #475569;
  --text-muted:     #64748b;
  --text-on-accent: #ffffff;

  --accent:         #1d4ed8;
  --accent-hover:   #1e40af;
  --accent-soft:    rgba(29,78,216,0.10);
  --accent-2:       #0e7490;

  --success:        #047857;
  --warning:        #b45309;
  --error:          #b91c1c;
  --error-soft:     rgba(185,28,28,0.08);

  --sidebar-bg:     #f6f7f9;
}
</style>
"""


def _inject_css() -> None:
    css_path = Path(__file__).parent / "styles" / "custom.css"
    if css_path.exists():
        with open(css_path, encoding="utf-8") as fh:
            css = fh.read()
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    # Swap the token values for light. Injected after custom.css so the
    # :root block here wins. Read from the widget key rather than a derived
    # variable, because this runs before _render_sidebar() creates the
    # control — on the rerun that follows a change, the widget's value is
    # already in session_state.
    if st.session_state.get("theme_choice", "Dark") == "Light":
        st.markdown(_LIGHT_TOKENS, unsafe_allow_html=True)


_inject_css()


# ── Sidebar ────────────────────────────────────────────────────────────────────

def _render_sidebar() -> None:
    """Sidebar with theme toggle and config health warnings."""
    with st.sidebar:
        st.markdown("## DocAgent")

        # ── Theme control ──────────────────────────────────────────────
        # ICON-AS-STATE, with both options always visible.
        #
        # The previous control was a single icon button showing the icon for
        # the theme you would GET (a sun while in dark mode). That conflates
        # state and action: a lone ☀ can equally be read as "you are in light
        # mode" or "press for light mode", and there was no label to settle it.
        #
        # Showing both options with the current one selected removes the
        # question entirely — you can see which theme is active AND what the
        # alternative is, with no hover or guesswork. A radio is also a
        # Streamlit primitive rather than a styled button, so it reruns on
        # change by itself and needs no manual st.rerun().
        #
        # The key is read directly by _inject_css(), which runs before this
        # function, so the value must live in the widget key, not a derived one.
        st.radio(
            "Theme",
            options=["Dark", "Light"],
            horizontal=True,
            label_visibility="collapsed",
            key="theme_choice",
        )
        st.divider()

        # ── Upload limit agreement ─────────────────────────────────────
        # Streamlit enforces server.maxUploadSize in the browser; validate_file
        # enforces app.max_file_size_mb after the bytes have already arrived. If
        # they disagree the user can sit through a long upload only to be told
        # the file is too big, so surface any drift rather than letting it be
        # discovered the slow way.
        cfg_issues = _cfg.validate()
        try:
            st_limit = int(st.get_option("server.maxUploadSize"))
            if st_limit != _cfg.max_file_size_mb:
                cfg_issues = cfg_issues + [
                    f"Upload limit mismatch: Streamlit accepts {st_limit} MB "
                    f"(server.maxUploadSize) but files over "
                    f"{_cfg.max_file_size_mb} MB are rejected after upload "
                    f"(app.max_file_size_mb). Set maxUploadSize in "
                    f".streamlit/config.toml to {_cfg.max_file_size_mb}."
                ]
        except Exception as exc:  # option unavailable in this Streamlit build
            logger.warning("Could not read server.maxUploadSize: %s", exc)

        # ── Config health check ────────────────────────────────────────
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
                help=(
                    "PDF, Excel (.xlsx / .xls), CSV, or Audio "
                    f"(MP3, WAV, M4A, FLAC, OGG, WebM) · max {_cfg.max_file_size_mb} MB per file"
                ),
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

    # Convert YouTube URL to stable file entry (name must survive reruns).
    #
    # Validation happens HERE, before a name is derived. Previously the name was
    # built from `extract_youtube_video_id(url) or hash(url)`, so an unusable URL
    # still produced an entry called something like `youtube_56152687.audio`, and
    # the results area rendered a heading for that non-existent document before
    # `_run_pipeline` got as far as rejecting it. The user saw a filename they
    # never supplied, for a document that was never going to exist.
    if input_mode == "YouTube URL" and youtube_url:
        from utils.file_handler import extract_youtube_video_id

        url_stripped = youtube_url.strip()
        # Gate on the VIDEO ID, not on is_valid_youtube_url(). That helper only
        # checks the domain, so `youtube.com/playlist?list=…` and a truncated
        # `watch?v=short` both pass it while yielding no id — which would name
        # the entry `youtube_None.audio` and then fail later inside yt-dlp.
        video_id = extract_youtube_video_id(url_stripped) if url_stripped else None

        if not url_stripped:
            files = []
        elif video_id:
            # Video ID as a stable key, so theme-toggle reruns don't generate a
            # new name and invalidate the cached result.
            files = [{"youtube_url": url_stripped, "name": f"youtube_{video_id}.audio"}]
        else:
            with col:
                if is_valid_youtube_url(url_stripped):
                    st.error(
                        "That is a YouTube link, but not to a single video. "
                        "Playlists, channels and shortened search links are not "
                        "supported — paste a `watch?v=…` or `youtu.be/…` link.",
                        icon=":material/link_off:",
                    )
                else:
                    st.error(
                        "That does not look like a YouTube link. Expected "
                        "`youtube.com/watch?v=…` or `youtu.be/…`.",
                        icon=":material/link_off:",
                    )
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
    "structured_extraction": "Extracting entities…",
}

# Progress is driven by the stage's POSITION in the frozen pipeline, not by how
# many callbacks have fired. The planner skips stages routinely (question
# extraction only runs for questionnaires, structure recognition only for
# table-heavy domains), so counting callbacks and dividing by a fixed total made
# the bar advance at the wrong rate and pair a percentage with the wrong label.
# These are the canonical stage numbers from CLAUDE.md, out of 6.
STAGE_POSITIONS = {
    "parse":                 1.0,
    "clean":                 2.0,
    "classify":              3.0,
    "structure_recognition": 3.5,
    "summarize":             4.0,
    "extract_questions":     5.0,
    "structured_extraction": 5.5,
}
TOTAL_STAGES = 6.0


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
        detail_placeholder   = st.empty()
        status_placeholder   = st.empty()

        with progress_placeholder.container():
            bar = st.progress(0, text="Starting…")

        start_ts = time.monotonic()

        # Walk back to the original _log_step regardless of how many times the
        # cached agent has been patched in previous runs.
        _orig_log = agent._log_step
        while hasattr(_orig_log, "__wrapped_orig__"):
            _orig_log = _orig_log.__wrapped_orig__  # type: ignore[attr-defined]

        completed: list = []
        bar_state = {"pct": 0.0}

        def _progress_log_step(skill_name, success, duration_ms, error=None):
            _orig_log(skill_name, success, duration_ms, error)

            # Position-based, so a planner-skipped stage does not shift the bar
            # out of step with its label. Unknown names fall back to the current
            # position rather than jumping the bar somewhere arbitrary.
            position = STAGE_POSITIONS.get(skill_name)
            pct = min(position / TOTAL_STAGES, 0.98) if position else bar_state["pct"]
            bar_state["pct"] = pct

            label = STEP_LABELS.get(skill_name, skill_name.replace("_", " ").title() + "…")
            stage_no = f"{position:g}" if position else "?"
            elapsed_s = time.monotonic() - start_ts

            if success:
                bar.progress(pct, text=f"Stage {stage_no}/6 · {label}  ({elapsed_s:.0f}s elapsed)")
                completed.append(f"✓ {label.rstrip('…')} — {duration_ms / 1000:.1f}s")
            else:
                # A failed stage must not read as progress.
                bar.progress(pct, text=f"Stage {stage_no}/6 · {label} FAILED")
                completed.append(f"✗ {label.rstrip('…')} — failed: {error}")

            detail_placeholder.caption("  ·  ".join(completed[-4:]))

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

        elapsed = time.monotonic() - start_ts
        bar.progress(1.0, text=f"Complete · {len(completed)} stage(s) in {elapsed:.1f}s")
        if completed:
            detail_placeholder.caption("  ·  ".join(completed))

        with status_placeholder.container():
            if result.success:
                st.session_state[state_key] = result
                st.session_state[f"parsed_doc_{name}"] = result.parsed_document
                # Auto-save to persistent history (non-blocking; errors are warnings).
                #
                # This step also embeds the document's chunks for search, which
                # is where the embedding model gets loaded. The first time in a
                # process that costs ~30s including the weights download, and it
                # happens *after* the progress bar already reads "Complete" — so
                # without a spinner the page simply freezes with no explanation.
                # Later saves are ~0.03s, so the wording adapts rather than
                # warning about a download that will not happen.
                from utils import embeddings as _emb
                if _emb.is_supported() and not _emb.is_loaded():
                    indexing_message = (
                        "Indexing document for search — first run only, "
                        "downloading the embedding model (~30s)…"
                    )
                else:
                    indexing_message = "Indexing document for search…"

                try:
                    raw_bytes = file_data.get("bytes")
                    with st.spinner(indexing_message):
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
        raw_text=d.get("raw_text", ""),  # restored from the store's own column
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

        # Seed the chat context from the stored chunks. Without this the chat
        # tab falls back to slicing raw_text, and for older entries that have no
        # stored content it would query an empty document without saying so.
        stored_chunks = cached_dict.get("content_chunks") or []
        if stored_chunks:
            st.session_state[f"chat_chunks_{file_name}"] = stored_chunks
        elif not result.raw_text:
            st.session_state[f"history_no_content_{file_name}"] = True
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
        # Uploader is in file mode but returned nothing — user cleared it, so
        # stale upload results should not linger.
        #
        # Only uploader-backed entries are dropped. An entry restored from the
        # history sidebar carries no "bytes", and wiping it here meant that
        # clicking a history item while the uploader was empty — the default
        # state — silently bounced straight back to the empty screen. The
        # restore ran, then this line undid it on the same rerun.
        surviving = [
            fd for fd in st.session_state.get("_file_data", []) if "bytes" in fd
        ]
        remaining = [
            fd for fd in st.session_state.get("_file_data", []) if "bytes" not in fd
        ]
        if remaining:
            st.session_state["_file_data"] = remaining
        elif surviving or "_file_data" in st.session_state:
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
