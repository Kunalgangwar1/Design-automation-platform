"""Paths and environment-backed defaults for the local project."""

import os
from pathlib import Path


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

# Dense-score thresholds are model- and corpus-specific and require empirical
# calibration. This starting value is intentionally visible and configurable.
FAISS_FALLBACK_SIMILARITY_THRESHOLD = float(
    os.getenv("FAISS_FALLBACK_SIMILARITY_THRESHOLD", "0.55")
)
DEFAULT_RETRIEVAL_MODE = "fallback"
DEFAULT_RRF_CONSTANT = 60
DEFAULT_DENSE_CANDIDATE_COUNT = 10
DEFAULT_LEXICAL_CANDIDATE_COUNT = 10

# boto3 obtains credentials from its normal provider chain; this project never
# accepts or persists AWS access keys.
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
BEDROCK_KNOWLEDGE_BASE_ID = os.getenv("BEDROCK_KNOWLEDGE_BASE_ID")
BEDROCK_RESULT_COUNT = int(os.getenv("BEDROCK_RESULT_COUNT", "5"))

VECTOR_INDEX_DIR = PROJECT_ROOT / "artifacts" / "faiss"
FAISS_INDEX_PATH = VECTOR_INDEX_DIR / "chunks.faiss"
FAISS_MAPPING_PATH = VECTOR_INDEX_DIR / "chunks.json"
