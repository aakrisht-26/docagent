"""Local sentence embeddings for document retrieval.

Design constraints, and why:

- **Local model, not an API.** Groq exposes no embeddings endpoint, so there is
  nothing to call. `all-MiniLM-L6-v2` is the default: 384 dimensions, ~90 MB,
  and fast enough on CPU that a 20-chunk document embeds in well under a second.
  A larger model would buy accuracy this corpus cannot demonstrate.

- **No vector database.** At this scale a document is tens of chunks, so a numpy
  dot product over a normalised matrix is exact, instant, and adds no service to
  install. A vector store would also break the fresh-clone story in the README.

- **Lazy loading.** Importing this module must stay free. The model is loaded on
  the first `encode()` call and cached thereafter, so `streamlit run` and
  `pytest` do not pay for it, and neither does any pipeline stage that never
  chats.

- **Degrades rather than fails.** If the model cannot be imported or loaded,
  `encode()` returns None and the caller falls back to keyword overlap. The
  failure is recorded so the load is not retried on every query.

Vectors are L2-normalised at encode time, so cosine similarity is a plain dot
product and `similarity()` needs no per-call normalisation.
"""

from __future__ import annotations

import base64
import threading
from typing import List, Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

#: Sentence-transformers model id. Change with care — stored vectors are only
#: comparable to vectors from the same model, and `DocumentStore` records this
#: name alongside them so mismatches can be detected rather than silently
#: producing nonsense similarities.
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
_DTYPE = np.float32

_model = None
_load_failed = False
_load_lock = threading.Lock()


def model_name() -> str:
    return MODEL_NAME


def is_supported() -> bool:
    """True if the library is importable. Does NOT load the model."""
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


def is_loaded() -> bool:
    """True if the model is already in memory, so the next encode is cheap.

    Lets callers word a progress message honestly: the first encode in a process
    takes ~30 s including the weights download, every later one takes
    milliseconds.
    """
    return _model is not None


def _get_model():
    """Load the model once, on first use. Returns None if it cannot be loaded."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None

    with _load_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            logger.warning(
                f"sentence-transformers unavailable ({exc}); "
                "retrieval will fall back to keyword overlap."
            )
            _load_failed = True
            return None

        try:
            logger.info(f"Loading embedding model '{MODEL_NAME}' (first use only)…")
            _model = SentenceTransformer(MODEL_NAME)
            logger.info(f"Embedding model '{MODEL_NAME}' ready.")
        except Exception as exc:
            # Most often no network on a first run, since the weights download
            # on demand and are cached under ~/.cache/huggingface afterwards.
            logger.warning(
                f"Could not load embedding model '{MODEL_NAME}' ({exc}); "
                "retrieval will fall back to keyword overlap."
            )
            _load_failed = True
            return None
    return _model


def encode(texts: List[str]) -> Optional[np.ndarray]:
    """Embed texts into an L2-normalised (n, EMBEDDING_DIM) float32 matrix.

    Returns None when the model is unavailable, which is the caller's signal to
    fall back rather than an error condition.
    """
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=_DTYPE)

    model = _get_model()
    if model is None:
        return None

    try:
        vectors = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,   # cosine becomes a dot product
            show_progress_bar=False,
        )
    except Exception as exc:
        logger.warning(f"Embedding failed ({exc}); falling back to keyword overlap.")
        return None

    return np.asarray(vectors, dtype=_DTYPE)


def similarity(query_vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one query against every row, as a 1-D array.

    Both sides are already normalised, so this is a dot product.
    """
    if matrix.size == 0:
        return np.zeros((0,), dtype=_DTYPE)
    return matrix @ np.asarray(query_vector, dtype=_DTYPE).reshape(-1)


# ── Persistence helpers ───────────────────────────────────────────────────────
#
# Stored as base64 of the raw float32 buffer. A 20-chunk document is 20 x 384 x
# 4 bytes = 30 KB, against roughly 90 KB as JSON, and round-trips exactly
# instead of through decimal text.

def to_blob(matrix: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(matrix, dtype=_DTYPE).tobytes()).decode("ascii")


def from_blob(blob: str, dim: int = EMBEDDING_DIM) -> Optional[np.ndarray]:
    if not blob:
        return None
    try:
        flat = np.frombuffer(base64.b64decode(blob), dtype=_DTYPE)
    except Exception as exc:
        logger.warning(f"Could not decode stored embeddings ({exc}); ignoring them.")
        return None
    if dim <= 0 or flat.size % dim:
        logger.warning(
            f"Stored embeddings are not a multiple of dim {dim} "
            f"({flat.size} floats); ignoring them."
        )
        return None
    return flat.reshape(-1, dim)
