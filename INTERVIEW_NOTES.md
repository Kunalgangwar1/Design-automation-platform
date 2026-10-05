# Interview Notes

## Business Problem


## Architecture

Current dummy architecture: custom ingestion, parsing, normalization, chunking, metadata extraction, embeddings, FAISS dense retrieval, BM25 lexical retrieval, RRF hybrid fusion, deterministic entity detection, bounded Neo4j enrichment, grounded-context construction, versioned prompt rendering, hosted generation, PostgreSQL run persistence, and a versioned FastAPI HTTP boundary.

AWS Kendra and Amazon Bedrock Knowledge Bases are not used. OpenAI and Gemini are generation providers only; neither performs retrieval.


## RAG

Day-10 evaluation is baseline-first: use a manually labeled fixed corpus, call the real retrievers, preserve negative results, and separate retrieval quality from generated-answer quality.

Recall@K matters because generation cannot reliably recover relevant evidence that never reaches the context. Precision@K matters because irrelevant chunks add noise and token cost. Hit@K answers whether at least one useful chunk is available. MRR measures how early the first relevant chunk appears.

The measured OMS baseline shows why hybrid is not automatically better. BM25 has the strongest MRR (0.825), while hybrid has the strongest Recall@10 (0.942). RRF combines rankings without understanding answer relevance and can move a relevant result down as well as up.

The optional CrossEncoder experiment is justified because hybrid retrieves all cases' first relevant evidence by K=10 but ranks some evidence poorly. Candidate retrieval maximizes recall cheaply; the CrossEncoder applies a more expensive query/chunk relevance model only to the hybrid top 10.

Measured hybrid → reranked results: Recall@1 0.308 → 0.425, Recall@3 0.683 → 0.708, Recall@5 0.783 → 0.858, Recall@10 remains 0.942, Hit@5 0.950 → 1.000, and MRR 0.721 → 0.831. Local average time changes from 10.97 ms to 425.77 ms: 10.56 ms candidate retrieval plus 415.18 ms reranking. Support API improves rank 4 → 3; gateway 5 → 3; requirement ID 7 → 5; shipping 2 → 1; outbox 2 → 1. Notification inputs regress 2 → 4.

Decision B: quality improves, but the CPU latency increase and observed regression do not justify enabling reranking by default. It remains optional. Reranking cannot fix low candidate recall because it only reorders chunks it receives.

Retrieval evaluation asks whether labeled chunks are found and ranked. Generation evaluation separately reports structural validity, provenance validity, expected-fact coverage, human answer relevance, human unsupported-claim notes, provider latency, and tokens. Provenance validity confirms cited IDs/paths came from supplied evidence; it does not prove every statement is factually correct.

Ten generation cases are prepared separately from retrieval labels. Only available genuine captures are evaluated; missing cases remain visible. A report with one capture says `10 prepared, 1 evaluated`, not “100% generation quality.” Expected-fact coverage uses transparent curated term groups and still requires human review.

## FAISS

FAISS handles semantic and paraphrase retrieval. Corpus and query vectors are L2-normalized, so exact inner-product search with `IndexFlatIP` corresponds to cosine similarity.


## BM25

BM25 handles exact terms such as API routes, error codes, event names, and document IDs. It uses small, deterministic tokenization rather than complex NLP preprocessing.


## Reciprocal Rank Fusion

RRF combines ranks rather than adding incompatible FAISS and BM25 scores. Each retriever contributes `1 / (rrf_constant + rank)` for a chunk.


## Kendra

Not used in this dummy project.


## Neo4j

Neo4j stores explicit OMS objects and directed relationships. The shared `Entity` label has a unique `name`; specific labels are `System`, `Service`, `Database`, `ExternalSystem`, `Interface`, `Event`, and `Process`.

Graph-enhanced RAG flow: hybrid document retrieval → known-name matching in sections/text → bounded neighborhood traversal → document evidence plus structured `GraphContext` → future context builder.

Traversal uses repeated parameterized one-hop Cypher queries rather than interpolating names or depth. Graph adjacency is relationship evidence for impact analysis, not proof that every adjacent component needs a change.

## PostgreSQL

PostgreSQL is the transactional system of record for versioned prompt templates, generation-request workflow state, and provider generation runs. Prompt versions support repeatability and auditability. Day 6 persists `CREATED → CONTEXT_READY`; Day 7 persists `GENERATED` only after provider success and validation, otherwise `FAILED`.

Day-6 flow: requirement → hybrid retrieval → entity detection → Neo4j enrichment → `ContextBuilder` → active PostgreSQL prompt template → `PromptBuilder` → `CONTEXT_READY`.

The database layer uses SQLAlchemy with Psycopg. Credentials come from `DATABASE_URL`; normal unit tests use SQLite and stubs rather than requiring live infrastructure.


## Context Builder

The context builder is the stable boundary between retrieval/graph infrastructure and future generation. It preserves final retrieval order and source provenance, applies a simple document-chunk limit, deduplicates graph relationships, and returns separate `DocumentEvidence` and `GraphEvidence` collections.


## Prompt Builder

The prompt builder loads versioned content from PostgreSQL and deterministically renders requirement, document evidence, graph evidence, and requested-output blocks. It does not invoke an LLM. The initial prompt requires evidence-only output, treats retrieved content as untrusted data rather than instructions, distinguishes facts from possible impacts, rejects invented APIs/systems/relationships, and reports insufficient evidence.

## FastAPI

Day-8 HTTP flow: client → `/api/v1/generations` → Pydantic schema → injected `GenerationApplicationService` → existing preparation/generation services → PostgreSQL → transport response.

FastAPI is only the delivery layer. Routes contain no FAISS, BM25, RRF, Cypher, prompt rendering, provider SDK calls, or SQL. HTTP schemas remain separate from internal application models.

Lifespan initializes the embedding model, FAISS/BM25 retrieval, Neo4j driver, SQLAlchemy engine/pool, and provider clients once per process. Neo4j and the engine are closed at shutdown. A yielded dependency creates and closes one SQLAlchemy session per request.

`POST /api/v1/generations` is synchronous and returns `201`; `GET /api/v1/generations/{request_id}` reads the request and latest run without regenerating; `GET /health` is cheap liveness; `GET /ready` checks PostgreSQL, Neo4j, and loaded FAISS state without paid LLM calls. Versioning protects future contract changes, and `/docs` is generated from FastAPI/Pydantic OpenAPI schemas.

Error handlers return stable safe codes for not-found, provider, dependency, and unexpected failures without exposing raw exceptions or secrets. Normal `TestClient` tests override the application-service dependency and make no external calls.

## Lambda

Lambda is a plausible future event-driven ingestion trigger: S3 document event → Lambda → ingestion/index-refresh workflow. It is not used for synchronous generation because loading the embedding model/index, cold starts, persistent connections, and hosted-generation latency favor long-lived FastAPI processes.

## LLM

The provider-independent `LLMClient` is implemented by `OpenAIClient` and `GeminiClient`. OpenAI uses the Responses API with `instructions`, `input`, bounded output tokens, low reasoning effort, and no hosted retrieval tools. Gemini uses Generate Content with `system_instruction`, the rendered task prompt, and bounded output. Both map provider responses into the same `LLMResponse`.

Generation flow: `PreparedGenerationRequest` → `LLMRequest` → OpenAI → optional Gemini fallback → `LLMResponse` → deterministic validation → `generation_runs` → `GenerationResult`.

OpenAI and Gemini generate text only. Ingestion, embeddings, FAISS, BM25, RRF, Neo4j, context construction, and prompt management remain custom application code. Each provider attempt is persisted as a separate generation run.

Retries are bounded and limited to timeouts, connection failures, rate limits, and temporary server errors. Gemini fallback occurs only after OpenAI exhausts one of those retryable errors. Invalid credentials, invalid requests/models, configuration failures, and validation failures are deterministic and do not trigger fallback.

Structural validation checks empty/short output, provider-error strings, section-heading scope, invented cited source paths, and invented explicit chunk IDs. It does not prove semantic correctness or replace retrieval/generation evaluation.


## Production Considerations

Day-9 flow: correlated HTTP request → cached hybrid retrieval → graph enrichment or explicit document-only degradation → prompt → bounded provider retry/fallback → validation → required persistence → safe response.

Retries and fallback solve different problems. A retry calls the same provider again after a transient error with bounded exponential backoff and jitter. Fallback calls the next configured provider after retry exhaustion. Provider fallback is generation reliability, not retrieval fallback.

Failure matrix: retrieval unavailable → fail; graph unavailable → optional document-only degradation; primary provider unavailable → bounded retries and eligible fallback; all providers unavailable → fail; PostgreSQL unavailable → fail; validation failure → persist `FAILED` without retry/fallback.

The in-process TTL/LRU retrieval cache keys normalized query + mode/top-k + retrieval settings + corpus mapping version. It is per FastAPI instance and is invalidated by process/resource rebuild or a new mapping version. Redis and distributed invalidation are postponed.

Structured JSON logs carry request ID, stage, status, latency, safe counts, provider/model, fallback, cache, and token metadata. They exclude secrets, raw authorization data, complete document content, and credential-bearing URLs. Operational monitoring measures reliability and latency; Day-10 RAG evaluation measures retrieval and answer quality.

Horizontal shape: load balancer → FastAPI replicas → shared PostgreSQL/Neo4j/hosted providers. Each replica loads its own SentenceTransformer and FAISS index, trading replica memory for low-latency process-local reuse.

Operational monitoring and RAG-quality evaluation are different. Day 9 observes availability, latency, retries, fallback, and caching in live execution. Day 10 uses versioned labels and repeatable metrics to measure retrieval ranking and captured-generation quality offline.

## Interview Questions
