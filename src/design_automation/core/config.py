"""Paths and environment-backed defaults for the local project."""

import os
from pathlib import Path


def _environment_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().casefold() in {"1", "true", "yes", "on"}


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"

# Character counts keep inspection intuitive and do not depend on a tokenizer.
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 120

# This small public model is a local interview-project choice, not a claim about
# the model used by the original enterprise platform.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DEVICE = "cpu"
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_TOP_K = 5

DEFAULT_RETRIEVAL_MODE = "hybrid"
DEFAULT_RRF_CONSTANT = 60
DEFAULT_DENSE_CANDIDATE_COUNT = 10
DEFAULT_LEXICAL_CANDIDATE_COUNT = 10
DEFAULT_CONTEXT_DOCUMENT_LIMIT = int(os.getenv("CONTEXT_DOCUMENT_LIMIT", "5"))

# Reranking remains an opt-in evaluation layer; the application continues to
# use plain hybrid RRF unless a later decision explicitly enables it.
RERANKER_ENABLED = _environment_bool("RERANKER_ENABLED", False)
RERANKER_MODEL = os.getenv(
    "RERANKER_MODEL",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
)
RERANKER_CANDIDATE_K = int(os.getenv("RERANKER_CANDIDATE_K", "10"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_REQUIREMENT_LENGTH = int(os.getenv("MAX_REQUIREMENT_LENGTH", "20000"))

RETRIEVAL_CACHE_ENABLED = _environment_bool("RETRIEVAL_CACHE_ENABLED", True)
RETRIEVAL_CACHE_TTL_SECONDS = float(
    os.getenv("RETRIEVAL_CACHE_TTL_SECONDS", "300")
)
RETRIEVAL_CACHE_MAX_ENTRIES = int(
    os.getenv("RETRIEVAL_CACHE_MAX_ENTRIES", "128")
)

DATABASE_URL = os.getenv("DATABASE_URL")

LLM_PROVIDER_ORDER = tuple(
    provider.strip().casefold()
    for provider in os.getenv("LLM_PROVIDER_ORDER", "openai,gemini").split(",")
    if provider.strip()
)
LLM_MAX_RETRY_ATTEMPTS = int(
    os.getenv(
        "LLM_MAX_RETRY_ATTEMPTS",
        os.getenv("OPENAI_MAX_RETRY_ATTEMPTS", "2"),
    )
)
LLM_RETRY_DELAY_SECONDS = float(
    os.getenv(
        "LLM_RETRY_DELAY_SECONDS",
        os.getenv("OPENAI_RETRY_DELAY_SECONDS", "0.25"),
    )
)
LLM_RETRY_JITTER_SECONDS = float(os.getenv("LLM_RETRY_JITTER_SECONDS", "0.1"))
LLM_RETRY_MAX_DELAY_SECONDS = float(
    os.getenv("LLM_RETRY_MAX_DELAY_SECONDS", "2.0")
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
OPENAI_API_TIMEOUT_SECONDS = float(os.getenv("OPENAI_API_TIMEOUT_SECONDS", "60"))
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "2000"))
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_API_TIMEOUT_SECONDS = float(os.getenv("GEMINI_API_TIMEOUT_SECONDS", "60"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2000"))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_CONNECTION_TIMEOUT_SECONDS = float(
    os.getenv("NEO4J_CONNECTION_TIMEOUT_SECONDS", "10")
)
NEO4J_QUERY_TIMEOUT_SECONDS = float(
    os.getenv("NEO4J_QUERY_TIMEOUT_SECONDS", "10")
)
DEFAULT_GRAPH_TRAVERSAL_DEPTH = int(os.getenv("GRAPH_TRAVERSAL_DEPTH", "1"))
MAX_GRAPH_TRAVERSAL_DEPTH = 3
GRAPH_DEGRADATION_ENABLED = _environment_bool("GRAPH_DEGRADATION_ENABLED", True)

DATABASE_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10")
)
DATABASE_POOL_TIMEOUT_SECONDS = float(
    os.getenv("DATABASE_POOL_TIMEOUT_SECONDS", "10")
)
DATABASE_POOL_RECYCLE_SECONDS = int(
    os.getenv("DATABASE_POOL_RECYCLE_SECONDS", "1800")
)

VECTOR_INDEX_DIR = PROJECT_ROOT / "artifacts" / "faiss"
FAISS_INDEX_PATH = VECTOR_INDEX_DIR / "chunks.faiss"
FAISS_MAPPING_PATH = VECTOR_INDEX_DIR / "chunks.json"
