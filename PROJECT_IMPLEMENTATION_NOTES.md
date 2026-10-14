# Design Automation Platform — Implementation Notes

Interview preparation: see [INTERVIEW_GUIDE.md](INTERVIEW_GUIDE.md) for the implementation-grounded architecture walkthrough, question bank, metrics, failure scenarios, and historical-versus-reconstructed wording.

This repository is a small interview-preparation project inspired by an enterprise platform that generates Solution Design Documents (SDDs) from requirements, natural-language prompts, and existing organizational knowledge.

Day 1 established the project boundaries and a fictional business domain. Day 2 added deterministic ingestion and section-aware chunking. Day 3 added local dense embeddings and exact FAISS retrieval. Day 4 added BM25 and RRF hybrid retrieval. Day 5 added a Neo4j knowledge graph for explicit OMS relationships and retrieval-context enrichment. Day 6 added grounded-context construction, versioned prompt preparation, and PostgreSQL workflow persistence. Day 7 added provider-independent hosted generation. Day 8 exposed those application services through FastAPI. Day 9 added production-like reliability and observability. Day 10 adds repeatable offline retrieval and generation-quality evaluation without redesigning the RAG algorithm.

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

1. `api` validates and translates HTTP requests and responses without implementing business logic.
2. `ingestion` will load and normalize enterprise documents.
3. `retrieval` finds local chunks with FAISS and BM25, then fuses their rankings.
4. `graph` stores, queries, and enriches evidence with OMS relationships in Neo4j.
5. `generation` assembles stable evidence, renders versioned prompts, validates output, and calls configured hosted providers through a generic LLM boundary.
6. `database` persists prompt templates, request workflow state, and individual generation runs in PostgreSQL.
7. `models` contains shared domain concepts.
8. `core` contains application configuration.

Ingestion, retrieval, graph enrichment, context preparation, prompt rendering, and hosted generation are separate application layers. OpenAI and Gemini generate text only; neither performs retrieval or graph enrichment.

## Planned end-to-end flow

The current end-to-end flow is:

1. Ingest PDFs, Word documents, Confluence pages, SharePoint content, and existing SDDs.
2. Normalize and chunk the content while preserving source metadata.
3. Create embeddings and store them in a FAISS index.
4. Seed and query explicit enterprise entities and relationships in Neo4j.
5. Accept the partial-refund requirement through a FastAPI endpoint.
6. Retrieve relevant evidence with FAISS and BM25, then merge candidates using Reciprocal Rank Fusion.
7. Enrich the evidence with graph relationships and build a stable grounded context.
8. Load a versioned prompt template and persist the request as `CONTEXT_READY` in PostgreSQL.
9. Send the already-rendered prompt through the generic `LLMClient` boundary to OpenAI, with Gemini available after retryable OpenAI failures.
10. Structurally validate and persist the generated section for human review.

## Repository layout

```text
.
├── data/
│   ├── architecture/
│   ├── evaluation/
│   ├── existing_sdds/
│   ├── interfaces/
│   └── requirements/
├── src/design_automation/
│   ├── api/
│   │   ├── routes/
│   │   ├── app.py
│   │   ├── dependencies.py
│   │   ├── errors.py
│   │   └── schemas.py
│   ├── core/
│   ├── database/
│   ├── evaluation/
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
PYTHONPATH=src python3 -m design_automation.retrieval.inspect --mode dense \
  "How should we handle a payment gateway timeout during refund?"
```

Override retrieval settings when inspecting:

```bash
PYTHONPATH=src python3 -m design_automation.retrieval.inspect \
  --top-k 3 \
  "Which services react after a successful refund?"
```

The binary index is stored at `artifacts/faiss/chunks.faiss`. Its deterministic row-to-chunk mapping and embedding-model metadata are stored as readable JSON at `artifacts/faiss/chunks.json`. Both files are generated artifacts and are ignored by Git.

## Dense, lexical, and hybrid retrieval

Day 4 keeps the retrieval pipeline fully custom and local:

```text
Enterprise documents
  -> custom ingestion, parsing, normalization, chunking, and metadata
  -> DocumentChunk corpus
       ├── Sentence Transformers -> normalized FAISS IndexFlatIP ranking
       └── explainable tokenization -> BM25 ranking
  -> merge candidates by chunk ID
  -> Reciprocal Rank Fusion
  -> final hybrid top-k results
```

FAISS is responsible for semantic and paraphrase matching. BM25 improves exact lexical matching for API routes, error codes, document IDs, event names, and other technical terms. Both operate on the same ordered `DocumentChunk` corpus.

The three modes are:

- `dense` — Sentence Transformers and FAISS only.
- `lexical` — BM25 only.
- `hybrid` — retrieve independent FAISS and BM25 candidate pools and fuse their ranks. This is the canonical default.

BM25 tokenization lowercases terms, removes a short fixed stopword list, and retains technical identifiers such as `IDEMPOTENCY_CONFLICT`, `OMS-REF-001`, and API paths. It also indexes selected inherited metadata because document front matter is deliberately not emitted as a retrieval chunk.

Hybrid retrieval uses Reciprocal Rank Fusion:

```text
RRF(chunk) = sum(1 / (rrf_constant + rank)) for each ranking containing chunk
```

Candidates are merged and deduplicated by chunk ID. A chunk found by both retrievers receives both rank contributions; a chunk found by only one retriever remains eligible. Raw cosine similarity and BM25 scores are retained for inspection but are never added together because their scales are incompatible.

Configuration defaults live in `src/design_automation/core/config.py`:

- `DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`
- `DEFAULT_DENSE_CANDIDATE_COUNT = 10`
- `DEFAULT_LEXICAL_CANDIDATE_COUNT = 10`
- `DEFAULT_TOP_K = 5`
- `DEFAULT_RRF_CONSTANT = 60`
- `DEFAULT_RETRIEVAL_MODE = "hybrid"`

Run each mode from the project root:

```bash
uv run python -m design_automation.retrieval.inspect --mode dense \
  "How should we handle a payment gateway timeout during refund?"

uv run python -m design_automation.retrieval.inspect --mode lexical \
  "IDEMPOTENCY_CONFLICT"

uv run python -m design_automation.retrieval.inspect --mode hybrid \
  "What API should customer support use for a partial refund?"
```

Candidate pools and final result count are independently configurable:

```bash
uv run python -m design_automation.retrieval.inspect \
  --mode hybrid \
  --dense-candidates 12 \
  --lexical-candidates 15 \
  --top-k 5 \
  "Which service owns refund eligibility?"
```

### Manual comparison queries

Use these queries to compare dense, lexical, and hybrid behavior manually:

1. `How should we handle a payment gateway timeout during refund?`
2. `Which service owns refund eligibility?`
3. `What information is required to notify inventory after a refund?`
4. `What API should customer support use for a partial refund?`
5. `What existing design patterns should partial refunds reuse?`
6. `IDEMPOTENCY_CONFLICT`
7. `OrderItemsRefunded`
8. `OMS-REF-001`
9. `What should happen when the external financial provider does not respond?`
10. `What is the employee payroll policy?`

The payroll query is deliberately outside the corpus domain. FAISS will still return nearest vectors, so those results must not yet be interpreted as sufficient evidence. Formal retrieval evaluation and sufficiency handling belong to a later stage.

AWS Kendra and Amazon Bedrock Knowledge Bases are not used. Amazon Bedrock may be introduced only after the custom RAG pipeline is complete, for LLM generation or generation-model fallback.

## Neo4j graph enrichment

Day 5 adds structured relationship evidence alongside the unstructured document evidence:

```text
Query
  -> FAISS + BM25 + RRF document retrieval
  -> deterministic known-name detection in retrieved sections and text
  -> bounded Neo4j neighborhood traversal
  -> retrieved document evidence + GraphContext relationship evidence
  -> future context builder
```

FAISS and BM25 answer “which text is relevant?” Neo4j answers “which explicitly modeled enterprise objects are connected, and how?” A graph edge is evidence of a known relationship. It is not automatic proof that every adjacent component must be modified.

The graph uses these labels:

- `Entity` as the shared uniqueness label.
- `System`, `Service`, `Database`, `ExternalSystem`, `Interface`, `Event`, and `Process` as the simple ontology.

Relationship types are `CONTAINS`, `CALLS`, `WRITES_TO`, `PUBLISHES`, `CONSUMES`, `DELIVERED_VIA`, and `PARTICIPATES_IN`.

### Run Neo4j locally

Choose a local learning password and start Neo4j Community Edition:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='choose-a-local-password'
export GRAPH_TRAVERSAL_DEPTH=1

docker run --name design-automation-neo4j \
  --detach \
  --publish 7474:7474 \
  --publish 7687:7687 \
  --env NEO4J_AUTH="${NEO4J_USER}/${NEO4J_PASSWORD}" \
  neo4j:5-community
```

Neo4j Browser is available at `http://localhost:7474`; the Python driver uses Bolt on port `7687`. Credentials are read only from environment-backed configuration and are not stored in the project.

Seed the graph. Constraints and `MERGE` make repeat runs idempotent:

```bash
uv run python -m design_automation.graph.seed
```

Inspect one entity at one or two hops:

```bash
uv run python -m design_automation.graph.inspect \
  --entity "Payment Service" \
  --hops 1

uv run python -m design_automation.graph.inspect \
  --entity "Payment Service" \
  --hops 2
```

Run hybrid retrieval followed by graph enrichment:

```bash
uv run python -m design_automation.graph.inspect \
  --query "What services are impacted by partial refunds?" \
  --hops 2
```

### Query design

Entity lookup and relationship queries pass names separately as `$name` parameters:

```cypher
MATCH (entity:Entity {name: $name})
RETURN entity.name, entity.entity_type, entity.description, entity.owner
```

```cypher
MATCH (entity:Entity {name: $name})-[relationship]-(neighbor:Entity)
RETURN startNode(relationship), relationship, endNode(relationship)
```

Multi-hop traversal is implemented as a bounded Python breadth-first traversal over repeated parameterized one-hop queries. This avoids interpolating user input or a dynamic traversal depth into Cypher. The default depth is `1` and the current safety maximum is `3`.

### Optional integration test

Normal tests use stubs and do not require Neo4j. With a running, seeded instance:

```bash
RUN_NEO4J_INTEGRATION=1 uv run --extra dev pytest -m integration -q
```

AWS Kendra and Amazon Bedrock Knowledge Bases remain outside the retrieval architecture. Amazon Bedrock may be considered later for LLM generation or generation-model fallback only.

## Grounded context, prompts, and PostgreSQL

Day 6 introduces the application boundary that prepares—but does not generate—an SDD section:

```text
Requirement
  -> existing FAISS + BM25 + RRF hybrid retrieval
  -> deterministic known-entity detection
  -> existing bounded Neo4j enrichment
  -> ContextBuilder
       ├── ordered DocumentEvidence with source provenance
       └── deduplicated GraphEvidence
  -> active versioned prompt template from PostgreSQL
  -> PromptBuilder
  -> generation request status CONTEXT_READY
```

`GroundedContext` prevents FAISS indexes, BM25 internals, and raw Neo4j records from leaking into the future generation layer. `ContextBuilder` preserves retrieval order, limits document chunks using `CONTEXT_DOCUMENT_LIMIT` (default `5`), and keeps document and graph evidence separate. Graph relationships remain explicit evidence, not proof that every adjacent component must change.

`PromptBuilder` renders deterministic `REQUIREMENT`, `DOCUMENT EVIDENCE`, `GRAPH EVIDENCE`, and `TASK / OUTPUT SECTION` blocks. It does not invoke any model. The seeded `impact_analysis` system prompt requires the future model to use supplied evidence only, separate facts from possible impact, avoid invented systems/APIs/relationships, and identify insufficient evidence.

PostgreSQL stores three tables:

- `prompt_templates`: unique `(name, version)`, prompt text, active flag, and creation time.
- `generation_requests`: requirement, requested section, prompt-template reference, timestamps, and workflow status.
- `generation_runs`: one row per provider invocation, including model, output, validation-related failure state, latency, token usage, and provider request ID. A request may have multiple runs.

Day 6 transitions `CREATED → CONTEXT_READY`. Day 7 marks a request `GENERATED` only after provider success, non-empty output, and deterministic validation; otherwise it becomes `FAILED`.

### Run PostgreSQL locally

Use these credentials for local development only:

```bash
docker run --name design-automation-postgres \
  --detach \
  --publish 5432:5432 \
  --env POSTGRES_USER=design_user \
  --env POSTGRES_PASSWORD=design_password \
  --env POSTGRES_DB=design_automation \
  postgres:16

export DATABASE_URL='postgresql+psycopg://design_user:design_password@localhost:5432/design_automation'
```

Initialize the tables and idempotently seed the first prompt:

```bash
uv run python -m design_automation.database.initialize
uv run python -m design_automation.database.seed
```

With the Day-5 Neo4j environment variables configured and graph seeded, inspect the complete preparation flow:

```bash
uv run python -m design_automation.generation.inspect \
  --requirement "Enable partial refunds for individual items of completed orders." \
  --section impact_analysis \
  --hops 2
```

The command prints retrieved chunks, detected entities, graph relationships, the stored prompt name/version, both rendered prompt parts, and final `CONTEXT_READY` status. It never generates an answer.

## Hosted generation and provider fallback

Day 7 keeps the custom RAG pipeline inside this application and adds only hosted text generation:

```text
Custom application RAG
  ingestion -> chunking -> embeddings -> FAISS + BM25 -> RRF
  -> Neo4j enrichment -> GroundedContext
  -> PostgreSQL prompt template -> PromptBuilder

Generation only
  PreparedGenerationRequest
  -> generic LLMClient
  -> OpenAIClient / OpenAI Responses API / gpt-5.6-terra
  -> GeminiClient / Gemini Generate Content API / gemini-2.5-flash-lite
  -> generic LLMResponse
  -> deterministic validation
  -> generation_runs + request status
```

The OpenAI adapter follows the official [Responses API](https://developers.openai.com/api/docs/guides/text) and uses the SDK's `output_text` accessor rather than assuming a fixed raw output-array shape. The Gemini adapter uses Google's official [`google-genai` text-generation API](https://ai.google.dev/gemini-api/docs/generate-content/text-generation), including `system_instruction`, bounded output, response text, and usage metadata. `gemini-2.5-flash-lite` is the stable cost-efficient fallback model selected for this learning project.

Neither hosted provider is responsible for ingestion, embeddings, FAISS, BM25, RRF, Neo4j, context selection, or prompt-template persistence. Both adapters implement the same `LLMClient` contract, so generation-provider changes do not rewrite the custom RAG layers.

Configure generation without storing secrets in source control:

```bash
export OPENAI_API_KEY='your-api-key'
export GEMINI_API_KEY='your-gemini-api-key'
export LLM_PROVIDER_ORDER='openai,gemini'
export OPENAI_MODEL=gpt-5.6-terra
export OPENAI_API_TIMEOUT_SECONDS=60
export OPENAI_MAX_OUTPUT_TOKENS=2000
export OPENAI_REASONING_EFFORT=low
export GEMINI_MODEL=gemini-2.5-flash-lite
export GEMINI_API_TIMEOUT_SECONDS=60
export GEMINI_MAX_OUTPUT_TOKENS=2000
export LLM_MAX_RETRY_ATTEMPTS=2
export LLM_RETRY_DELAY_SECONDS=0.25
```

Both SDKs' internal retries are disabled. The application retries only timeout, connection, rate-limit, and temporary server failures using the small configured budget. If OpenAI still fails with one of those retryable errors, the service records the failed OpenAI run and tries Gemini. Authentication, configuration, invalid request/model, empty output, and validation failures neither retry nor trigger fallback. If both providers fail, the request becomes `FAILED`.

Run the existing Day-6 inspection without generation by omitting `--generate`. To generate with the configured `openai,gemini` order and persist every provider attempt:

```bash
uv run python -m design_automation.generation.inspect \
  --requirement "Enable partial refunds for individual items of completed orders." \
  --section impact_analysis \
  --hops 2 \
  --generate
```

Validation checks non-empty/minimum-length output, obvious provider-error text, requested-section heading scope, cited source paths, and explicit cited chunk IDs against `GroundedContext`. These deterministic checks do not prove factual correctness and are not a replacement for semantic RAG evaluation.

Normal tests never call a hosted API. Run optional live adapter smoke tests only when intended:

```bash
RUN_OPENAI_INTEGRATION=1 uv run --extra dev pytest \
  tests/test_openai_integration.py -q

RUN_GEMINI_INTEGRATION=1 uv run --extra dev pytest \
  tests/test_gemini_integration.py -q
```

## FastAPI HTTP boundary

Day 8 adds a delivery layer around the existing application:

```text
Client
  -> FastAPI /api/v1
  -> GenerationApplicationService
  -> GenerationOrchestrator
  -> custom FAISS + BM25 + RRF retrieval
  -> Neo4j enrichment
  -> context and prompt preparation
  -> GenerationService and configured hosted providers
  -> deterministic validation and PostgreSQL persistence
  -> transport-only GenerationResponse
```

FastAPI does not perform RAG, issue Cypher, render prompts, invoke provider SDKs, or execute SQL inside route functions. Pydantic request/response schemas are separate from internal models such as `GroundedContext`, `PreparedGenerationRequest`, `RetrievalResult`, and `GenerationResult`.

Business endpoints use `/api/v1`, allowing a future incompatible HTTP contract to coexist without changing existing clients. FastAPI generates OpenAPI automatically at [http://localhost:8000/docs](http://localhost:8000/docs).

### Resource and session lifecycle

FastAPI lifespan initializes the sentence-transformer, FAISS index/mapping, BM25 corpus, Neo4j driver, SQLAlchemy engine/pool, and configured LLM clients once per server process. The Neo4j driver and database engine are closed during shutdown. Each HTTP request receives its own SQLAlchemy `Session`, which is closed after that request; mutable sessions are never shared globally. `/health` returns only process liveness and performs no database, graph, retrieval, or paid-provider call.

### Run the API

Load the ignored local `.env` before starting the server:

```bash
set -a
source .env
set +a

uv run uvicorn design_automation.api.app:app --reload
```

Create a generation synchronously:

```bash
curl --request POST 'http://localhost:8000/api/v1/generations' \
  --header 'Content-Type: application/json' \
  --data '{
    "requirement": "Enable partial refunds for individual items of completed orders.",
    "section": "impact_analysis",
    "graph_hops": 2
  }'
```

Retrieve persisted request state and its latest provider run, replacing the ID with the POST response's `request_id`:

```bash
curl 'http://localhost:8000/api/v1/generations/REQUEST_ID'
```

Check lightweight health:

```bash
curl 'http://localhost:8000/health'
```

Check dependency readiness without making a paid model call:

```bash
curl 'http://localhost:8000/ready'
```

Pydantic rejects malformed input with `422`. Unknown generation IDs return `404`. Temporary provider or infrastructure failures return safe `503` responses, deterministic provider failures return `502`, and unexpected failures return a generic `500`. Raw SDK, SQLAlchemy, Neo4j, credential, and stack-trace details are not returned.

Normal API tests use `TestClient`, dependency overrides, and a fake application facade:

```bash
uv run --extra dev pytest tests/test_api.py -q
```

## Production-like reliability and observability

Day 9 keeps the algorithms and application boundaries unchanged while adding this operational flow:

```text
HTTP request + X-Request-ID
  -> correlated JSON logs and total timer
  -> process-local retrieval cache
  -> FAISS + BM25 + RRF on a cache miss
  -> Neo4j enrichment, or explicit document-only degradation
  -> grounded prompt preparation
  -> bounded same-provider retries with backoff and jitter
  -> configured cross-provider fallback
  -> validation and required PostgreSQL persistence
  -> safe correlated HTTP response
```

`X-Request-ID` is accepted only when it is short and contains safe printable identifier characters; otherwise the application creates a UUID. It is an observability value, never an authentication or authorization identifier. Every response includes it, and safe error bodies include it for support correlation.

Important runtime events are emitted as one JSON object per Python log record. Fields cover stage/status/latency, request and generation IDs, counts, provider/model, fallback, cache hit/miss, and token usage. Query text, document content, credentials, authorization headers, and credential-bearing URLs are deliberately excluded.

The optional retrieval cache is a per-process, thread-safe TTL/LRU cache over final retrieval responses. Its key contains the whitespace-normalized/case-folded query, mode, top-k, retrieval settings, and a SHA-256 version of the persisted FAISS chunk mapping. A changed mapping therefore uses a new key namespace after restart; rebuilding resources clears the in-memory cache. This is intentionally not distributed invalidation and not an LLM-response cache.

Failure policy:

| Failure | Policy |
|---|---|
| FAISS/BM25/RRF retrieval unavailable | Fail; never send an ungrounded prompt |
| Neo4j temporarily unavailable | Log degradation and continue with document evidence only when enabled |
| Primary LLM transiently unavailable | Bounded same-provider retry, then configured provider fallback |
| All eligible LLM providers unavailable | Fail |
| PostgreSQL unavailable | Fail; traceability is required |
| Deterministic validation fails | Persist `FAILED`; do not retry or fall back |

A retry repeats the same provider after a transient error. Fallback selects a different configured provider after the retry policy is exhausted. Generation-provider fallback is unrelated to the FAISS/BM25 hybrid retrieval algorithm.

`GET /health` is cheap liveness. `GET /ready` verifies PostgreSQL, Neo4j, and the already-loaded FAISS corpus without calling OpenAI or Gemini. PostgreSQL has explicit connect/pool timeouts; Neo4j has explicit connection and transaction timeouts; hosted providers retain their explicit API timeouts.

The seeded system instruction treats retrieved documents and graph data as untrusted evidence, ignores instructions embedded in that evidence, and retains the existing evidence-only/non-invention rules. Rerun the idempotent seed after updating the project:

```bash
uv run python -m design_automation.database.seed
```

For horizontal scaling, a load balancer can distribute requests across multiple FastAPI instances backed by shared PostgreSQL, shared Neo4j, and hosted LLM providers. Each instance currently loads its own SentenceTransformer and FAISS index. That improves per-process reuse but consumes memory per replica; replacing this local index is deliberately out of scope.

AWS Lambda is not used for the synchronous RAG generation path because model/index initialization, cold starts, persistent connections, and generation latency work against that workload. A realistic later role is `new S3 document/event -> Lambda ingestion trigger -> asynchronous ingestion/index-refresh workflow`.

Operational monitoring in Day 9 measures availability, fallback, cache behavior, latency, and tokens. It is not RAG-quality evaluation.

## Offline RAG evaluation

Day 10 introduces a manually curated, versioned gold dataset at `data/evaluation/oms_rag_gold.json`. Its 20 queries use only the current 23-chunk OMS corpus and cover semantic paraphrase, exact identifiers, APIs, business rules, ownership, failures, architecture relationships, events, security/audit, existing-design reuse, and multi-evidence questions. Every relevant chunk ID is checked against a fresh ingestion result before evaluation, so stale or invented labels fail immediately.

The retrieval evaluator calls the existing dense, lexical, and hybrid coordinator modes at a single candidate depth of 10 and derives all smaller cutoffs from that ranking. It does not duplicate retrieval logic. Metrics are:

```text
Precision@K = relevant retrieved chunks in top K / K
Recall@K    = relevant retrieved chunks in top K / all labeled relevant chunks
Hit@K       = 1 when any labeled relevant chunk occurs in top K, otherwise 0
RR@10       = 1 / first relevant rank within top 10, otherwise 0
MRR@10      = mean RR@10 across all queries
```

The immutable Day-10 baseline artifact is written to `artifacts/evaluation/retrieval_baseline.json`; rerunning without `--no-save` will not overwrite it. The measured baseline on the current corpus is:

| Mode | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Hit@5 | MRR@10 |
|---|---:|---:|---:|---:|---:|---:|
| Dense FAISS | 0.158 | 0.483 | 0.742 | 0.842 | 0.950 | 0.559 |
| BM25 lexical | 0.442 | 0.700 | 0.833 | 0.883 | 1.000 | 0.825 |
| Hybrid RRF | 0.308 | 0.683 | 0.783 | 0.942 | 0.950 | 0.721 |

Higher K improves recall but generally lowers precision and would increase generation-context/token cost. The baseline therefore does not justify changing the application default automatically. For the known support API query, the expected `doc-e14e6a30041c-chunk-0001` ranks 5th in dense, 4th in BM25, and 4th in hybrid retrieval.

Because hybrid finds every case's first relevant chunk by K=10 but has weaker MRR, an optional local experiment reranks the unchanged hybrid top-10 with `cross-encoder/ms-marco-MiniLM-L-6-v2`. Original dense/BM25 ranks, scores, RRF score, metadata, and provenance remain attached; `reranker_score` is a separate diagnostic.

| Mode | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Hit@5 | Hit@10 | MRR@10 | Average latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hybrid RRF | 0.308 | 0.683 | 0.783 | 0.942 | 0.950 | 1.000 | 0.721 | 10.97 ms |
| Hybrid + CrossEncoder | 0.425 | 0.708 | 0.858 | 0.942 | 1.000 | 1.000 | 0.831 | 425.77 ms |

The reranked total separates into approximately 10.56 ms of hybrid candidate retrieval and 415.18 ms of CrossEncoder scoring on this local run. The support API rank improves 4 → 3, gateway timeout 5 → 3, `OMS-REF-001` 7 → 5, shipping 2 → 1, and transactional outbox 2 → 1. `notification-refund-inputs` regresses 2 → 4. These are local diagnostics rather than a controlled benchmark.

Decision B applies: reranking improves aggregate ordering, but its roughly 39-times higher latency and one observed regression do not justify enabling it by default for this small corpus. It remains an opt-in experiment. Candidate retrieval aims for inexpensive recall; the more expensive CrossEncoder performs pairwise query/chunk ordering only on the top 10. It cannot recover a chunk missing from those candidates.

Environment-backed experimental defaults are:

```text
RERANKER_ENABLED=false
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RERANKER_CANDIDATE_K=10
```

`RERANKER_ENABLED` documents the adoption decision; the evaluation CLI still requires the explicit `--reranker-model` flag. The production retrieval coordinator is unchanged.

Run the reproducible retrieval baseline without paid calls:

```bash
uv run python -m design_automation.evaluation.retrieval_eval --no-save
```

Run the optional CrossEncoder comparison (the model may be downloaded once):

```bash
uv run python -m design_automation.evaluation.retrieval_eval \
  --reranker-model \
  --no-save
```

The per-query report prints expected IDs, each mode's first relevant rank, Hit@5, top IDs, known-case before/after ranks, and all regressions. The separate experiment artifact is `artifacts/evaluation/retrieval_hybrid_reranked.json`; neither it nor later experiments overwrite `retrieval_baseline.json`.

### Human-first generation evaluation

Ten manually prepared generation cases live separately at `data/evaluation/oms_generation_cases.json`, leaving retrieval relevance labels unchanged. They cover the support API, ownership, completed-order eligibility, shipping, gateway reconciliation, `OrderItemsRefunded`, inventory inputs, audit retention, PII/card restrictions, and cancellation versus partial refund. The generation evaluator consumes previously captured answers; it never calls OpenAI or Gemini. A capture records the case, provider/model, generated text, latency/tokens, the exact supplied chunk IDs and source paths, optional human answer-relevance judgment, and any human-identified unsupported claims:

```json
{
  "results": [
    {
      "case_id": "support-partial-refund-api",
      "requested_section": "impact_analysis",
      "provider": "gemini",
      "model": "configured-model-name",
      "generated_text": "captured answer",
      "latency_ms": 1000,
      "input_tokens": 500,
      "output_tokens": 150,
      "supplied_chunk_ids": ["doc-e14e6a30041c-chunk-0001"],
      "supplied_source_paths": ["interfaces/refund_interfaces.md"],
      "answer_relevance": "not_reviewed",
      "unsupported_claims": [],
      "unsupported_claims_reviewed": false
    }
  ]
}
```

Save captures to `artifacts/evaluation/generation_results.json`, then run:

```bash
uv run python -m design_automation.evaluation.generation_eval \
  --results artifacts/evaluation/generation_results.json \
  --no-save
```

The report prints prepared, evaluated, and missing-result counts. Each expected fact is shown as covered or not covered; aggregate coverage is covered facts divided by all expected facts among available captures. Structural validity, provenance validity, human relevance, human unsupported-claim review, provider latency, and token usage remain separate dimensions. Provenance validity proves only that explicit cited chunks/paths were supplied; it does not prove factual correctness. No single artificial “RAG score” or paid LLM judge is used.

The currently saved capture file contains one result, so the honest report is `10 prepared, 1 evaluated, 9 missing`. The evaluator does not fabricate the other nine outputs or claim that one passing result represents overall generation quality. Live provider population is not implemented in this evaluator; genuine outputs can be collected later through the existing generation flow and added explicitly.

## Day 11 boundary

Authentication/JWT, RBAC, rate limiting, Redis, queues/background workers, Celery, Kafka, SQS, actual Lambda deployment, circuit-breaker frameworks, Prometheus/Grafana, a production reranker rollout, LLM-as-judge, RAGAS, large-scale benchmarking, application Docker/Kubernetes deployment, and cloud hosting remain deliberately postponed.
