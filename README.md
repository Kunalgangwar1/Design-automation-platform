# Design Automation Platform

This repository is a small interview-preparation project inspired by an enterprise platform that generates Solution Design Documents (SDDs) from requirements, natural-language prompts, and existing organizational knowledge.

Day 1 established the project boundaries and a fictional business domain. Day 2 added deterministic loading, normalization, metadata extraction, and structure-aware chunking. Day 3 added local dense embeddings and exact FAISS semantic retrieval. Day 4 adds Amazon Bedrock Knowledge Bases as the managed fallback path. The earlier BM25/RRF experiment remains available only as a local diagnostic. The repository still contains no graph database, relational database, web API, or LLM integration.

## Business problem

The fictional company operates an Order Management System (OMS). Customer support can cancel an order before fulfilment, but it cannot refund only selected items after an order is complete. Agents currently calculate and process these cases manually, which creates inconsistent refund amounts, weak auditability, and the risk that inventory and customer notifications fall out of sync.

The main requirement is:

> Enable partial refunds for individual items of a completed order.

The future platform should use this requirement and enterprise source material to produce an evidence-based SDD. The sample documents under `data/` represent that source material.

## Existing enterprise architecture

The sample OMS contains:

- **Order Service** — owns the order lifecycle and refund orchestration.
- **Payment Service** — owns payment and refund transactions.
- **Inventory Service** — records whether returned stock is restockable.
- **Notification Service** — sends customer refund notifications.
- **Order Database** — stores orders, line items, and refund audit records.
- **External Payment Gateway** — executes card refunds and returns provider references.

The Order Service is the system entry point for a partial refund. It validates completed orders and refundable quantities, calls the Payment Service, records the result in the Order Database, and publishes a refund event. Inventory and Notification consume that event for their own responsibilities.

## Proposed platform architecture

The intended design-automation architecture is represented by package boundaries under `src/design_automation/`:

1. `api` will accept requirements and SDD-generation requests.
2. `ingestion` will load and normalize enterprise documents.
3. `retrieval` finds local chunks with FAISS and can fall back to Amazon Bedrock Knowledge Bases.
4. `graph` will store and query system relationships in Neo4j.
5. `generation` will assemble evidence and ask an LLM to draft an SDD.
6. `database` will persist metadata, jobs, and generated-document records in PostgreSQL.
7. `models` contains shared domain concepts.
8. `core` contains application configuration.

These are planned responsibilities, not implemented integrations.

## Planned end-to-end flow

When implemented in later stages, the flow will be:

1. Ingest PDFs, Word documents, Confluence pages, SharePoint content, and existing SDDs.
2. Normalize and chunk the content while preserving source metadata.
3. Create embeddings and store them in a FAISS index.
4. Extract entities and relationships into Neo4j.
5. Accept the partial-refund requirement through a FastAPI endpoint.
6. Retrieve relevant evidence from FAISS, using Bedrock Knowledge Bases when local results are insufficient.
7. Enrich the evidence with graph relationships and PostgreSQL metadata.
8. Prompt an LLM to draft a traceable SDD.
9. Validate, persist, and return the generated document for human review.

## Repository layout

```text
.
├── data/
│   ├── architecture/
│   ├── existing_sdds/
│   ├── interfaces/
│   └── requirements/
├── src/design_automation/
│   ├── api/
│   ├── core/
│   ├── database/
│   ├── generation/
│   ├── graph/
│   ├── ingestion/
│   ├── models/
│   └── retrieval/
└── tests/
```

## Local setup

With Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Deterministic ingestion

The implemented Day 2 flow is:

```text
Markdown/text file -> loader -> normalized Document -> chunker -> DocumentChunk list
```

Documents are discovered recursively beneath `data/` in source-path order. Loaders normalize line endings, trailing whitespace, and repeated blank lines without rewriting headings, lists, code blocks, API paths, or identifiers. Markdown metadata is extracted only from a small explicit set of bold labels.

Markdown is split by headings first. Sections that fit within the configured maximum stay intact. Only oversized sections receive character overlap while being split near paragraph, line, sentence, or word boundaries.

Defaults live in `src/design_automation/core/config.py`:

- `DEFAULT_CHUNK_SIZE = 1200`
- `DEFAULT_CHUNK_OVERLAP = 120`

They can be overridden with `ChunkingConfig` in Python or command-line flags during inspection.

After installing the project, inspect exactly what a future retrieval index would receive:

```bash
inspect-chunks
```

Without installing it, run the same command from the repository root:

```bash
PYTHONPATH=src python3 -m design_automation.ingestion.inspect
```

Optional inspection settings:

```bash
PYTHONPATH=src python3 -m design_automation.ingestion.inspect --max-chars 800 --overlap-chars 80
```

Run tests with:

```bash
python3 -m pytest
```

## Dense semantic retrieval

Day 3 sends each retrievable `DocumentChunk` through a configurable local Sentence Transformers model. The embedding input includes document title, section heading, and chunk text. The default model is `sentence-transformers/all-MiniLM-L6-v2`, which produces 384-dimensional embeddings. CPU is the portable default device and can be overridden with `--device`. This is an interview-project choice and does not imply that the original enterprise platform used the same model.

Corpus embeddings and query embeddings are converted to contiguous `float32` matrices and L2-normalized. FAISS `IndexFlatIP` then performs exact inner-product search. Because both sides have unit length, that inner product is cosine similarity.

Build or rebuild the persisted index:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect --rebuild
```

Run a local FAISS-only query:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode faiss \
  "How should we handle a payment gateway timeout during refund?"
```

Override retrieval settings when inspecting:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect \
  --top-k 3 \
  "Which services react after a successful refund?"
```

The binary index is stored at `artifacts/faiss/chunks.faiss`. Its deterministic row-to-chunk mapping and embedding-model metadata are stored as readable JSON at `artifacts/faiss/chunks.json`. Both files are generated artifacts and are ignored by Git.

## Managed fallback retrieval

There are two important architecture statements, and they should not be blurred together:

- **Historical enterprise architecture:** the original project remembered for this exercise used AWS Kendra as its managed enterprise-search fallback.
- **Current reconstruction:** this repository uses Amazon Bedrock Knowledge Bases for the managed fallback. AWS placed Kendra in maintenance mode on June 30, 2026, stopped accepting new customers on July 30, 2026, and recommends Bedrock Knowledge Bases for new search applications. See the [AWS Kendra availability announcement](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html).

The canonical current request flow is:

```text
Enterprise sources
  -> Bedrock Knowledge Base managed ingestion/indexing

Curated local documents
  -> custom deterministic ingestion/chunking
  -> local MiniLM embeddings
  -> normalized IndexFlatIP FAISS index

Query
  -> FAISS primary retrieval
  -> sufficiency decision
       -> top score meets threshold: return FAISS results
       -> no result or top score below threshold: call Bedrock KB Retrieve
  -> common RetrievalResult objects
  -> future Neo4j enrichment
  -> future prompt construction
  -> future LLM SDD generation
```

The adapter calls the Bedrock Agent Runtime `Retrieve` API, not `RetrieveAndGenerate`; it performs retrieval only and makes no LLM call. Bedrock results retain provider text, location, metadata, score, document identifier, and `provider="bedrock_kb"`. FAISS and Bedrock scores are never combined because their meanings and scales are provider-specific.

The canonical modes are:

- `faiss` — local normalized MiniLM + FAISS only.
- `bedrock` — Bedrock Knowledge Bases `Retrieve` only.
- `fallback` — FAISS first; invoke Bedrock only when FAISS returns no results or its top score is below the configured threshold. This is the default.

The threshold defaults to `0.55`. It is a visible starting point that requires empirical calibration for the model, corpus, and evaluation set; it is not a universal relevance score. Identifier-shaped queries do not independently trigger Bedrock fallback.

Configure Bedrock with environment variables. The values below are placeholders:

```bash
export AWS_REGION=us-east-1
export BEDROCK_KNOWLEDGE_BASE_ID=YOUR_KNOWLEDGE_BASE_ID
export BEDROCK_RESULT_COUNT=5
export FAISS_FALLBACK_SIMILARITY_THRESHOLD=0.55
```

Do not put credentials in the repository. boto3 uses its standard credential provider chain, including configured AWS profiles, IAM Identity Center/SSO, environment credentials, container credentials, and IAM roles.

Run the canonical modes:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode faiss \
  "How should we handle a payment gateway timeout during refund?"

PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode bedrock \
  "What API should customer support use for a partial refund?"

PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode fallback \
  "Which service owns refund eligibility?"
```

Missing Knowledge Base, region, credential, permission, and service configuration errors are reported by the CLI without silently switching providers.

### Optional local diagnostics

The earlier `rank-bm25` and Reciprocal Rank Fusion implementation remains useful for local inspection and interview discussion, but it is not the canonical managed fallback and is not equivalent to Kendra or Bedrock Knowledge Bases:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode lexical \
  "What does IDEMPOTENCY_CONFLICT mean?"

PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode hybrid \
  "Which service owns refund eligibility?"
```

Hybrid diagnostic mode combines FAISS and BM25 ranks using Reciprocal Rank Fusion; it never adds raw cosine and BM25 scores.

Configuration defaults live in `src/design_automation/core/config.py`. Bedrock settings and the FAISS fallback threshold are environment-backed; local RRF candidate settings remain code defaults for diagnostics.

## Day 5 boundary

Neo4j, PostgreSQL, FastAPI, LangChain, Lambda, LLM calls, SDD generation, learned reranking, application-level access control, Bedrock Knowledge Base provisioning/synchronization, Docker, and AWS deployment remain deliberately postponed. No Kendra client is implemented; Kendra remains part of the historical architecture description only.
