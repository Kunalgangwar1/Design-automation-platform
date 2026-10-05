# Design Automation Platform — Interview Guide

This guide describes the code in this repository as it exists after Day 10. Use it to explain the reconstruction accurately, not to claim that every future production option is already implemented.

## 1. Project introduction

### One-sentence description

A grounded Solution Design Document platform that retrieves evidence from enterprise documents with custom FAISS/BM25 hybrid retrieval, enriches it with explicit Neo4j relationships, renders a versioned PostgreSQL prompt, calls a hosted LLM through a provider-neutral interface, validates provenance, and exposes the workflow through FastAPI.

### 30-second interview answer

“I rebuilt a simplified design-automation platform around a fictional Order Management System. It ingests and section-chunks enterprise documents, uses 384-dimensional Sentence Transformer embeddings with FAISS plus BM25 lexical search, and combines rankings with Reciprocal Rank Fusion. Retrieved text is enriched with bounded Neo4j relationships, converted into a stable `GroundedContext`, and rendered using versioned prompt templates from PostgreSQL. A provider-independent generation service calls configured OpenAI or Gemini adapters, validates explicit chunk and source citations, persists each run, and exposes the workflow through FastAPI. I also built an offline gold-set evaluation, which showed strong hybrid candidate recall but weaker ordering, so I measured an optional CrossEncoder reranker rather than assuming it helped.”

### Two-minute architecture explanation

The business problem is to generate grounded SDD sections from requirements, architecture descriptions, API contracts, and earlier SDDs. The dummy requirement is to enable item-level partial refunds for completed orders across Order, Payment, Inventory, and Notification services.

At ingestion time, Markdown and text files are loaded into a common `Document`, conservatively normalized, assigned stable path-derived IDs, and split by Markdown headings. Sections stay intact unless they exceed 1,200 characters; only then are they split with 120-character overlap. Front-matter-only title blocks are retained as document metadata but are not retrieval chunks.

For retrieval, `all-MiniLM-L6-v2` creates 384-dimensional vectors. Corpus and query vectors are L2-normalized and searched with FAISS `IndexFlatIP`, making inner product equivalent to cosine similarity. In parallel, BM25 indexes explainable tokens, including routes and identifiers such as `IDEMPOTENCY_CONFLICT` and `OMS-REF-001`. `RetrievalCoordinator` retrieves independent candidate lists and fuses ranks using RRF with constant 60. The production API uses this hybrid path. A CrossEncoder top-10 reranker exists only as an opt-in Day-10 evaluation experiment.

The selected chunks are scanned deterministically for names from the fixed OMS graph catalog. Neo4j returns bounded one- or two-hop relationship evidence. `ContextBuilder` keeps ordered document evidence separate from deduplicated graph evidence. `PromptBuilder` renders that context using the active versioned PostgreSQL prompt. `GenerationService` handles bounded same-provider retries, eligible provider fallback, deterministic validation, and generation-run persistence. FastAPI remains a thin transport layer with process-wide model/index resources and per-request database sessions.

### Five-minute deep walkthrough

1. **Corpus and ingestion.** Four small OMS source documents represent requirements, architecture, interfaces, and a previous cancellation SDD. `ingest_documents()` discovers supported `.md`, `.markdown`, and `.txt` files in stable path order. Loaders create `Document` objects with title, content, source path/type, inferred document type, and explicit metadata such as requirement ID, owner, status, and review date.
2. **Chunking.** `chunk_document()` parses headings while respecting fenced code blocks. A heading and body remain together where possible. Oversized sections split at paragraphs, lines, sentences, then words. Title/front-matter-only sections are not emitted, while their metadata is copied to each content chunk. IDs have the form `doc-<path-hash>-chunk-0000`.
3. **Dense retrieval.** `SentenceTransformerEmbeddingService` embeds title, section, and text. `FaissVectorStore` normalizes vectors and uses exact `IndexFlatIP`. The binary index and readable row-to-chunk JSON mapping are persisted separately.
4. **Lexical retrieval.** `BM25Retriever` tokenizes normal words, technical identifiers, and API routes. It lowercases terms, removes a small stopword set, and expands hyphenated/underscored identifiers into components. Selected inherited metadata is indexed because front matter is not a retrieval chunk.
5. **Hybrid retrieval.** `RetrievalCoordinator` requests up to 10 dense and 10 lexical candidates, deduplicates by chunk ID, and applies `1 / (60 + rank)` per contributing retriever. It never adds FAISS and BM25 raw scores because those scales are unrelated.
6. **Evaluation and reranking.** Twenty manually labeled retrieval cases measure Precision, Recall, Hit Rate, and MRR at K=1/3/5/10. Hybrid Recall@10 is 0.942 and Hit@10 is 1.0, but MRR is 0.721 versus BM25’s 0.825. That justified testing a top-10 CrossEncoder. It improves MRR to 0.831 but raises local average latency to about 426 ms and regresses one query, so it remains disabled by default.
7. **Graph enrichment.** The entity matcher scans every retrieved section and full chunk text against all 12 canonical graph names, case-insensitively. `GraphRepository` uses parameterized one-hop Cypher and repeated bounded breadth-first traversal. Neo4j contributes explicit relationship evidence; adjacency is not treated as proof of change impact.
8. **Grounding and prompts.** `ContextBuilder` converts infrastructure results into `DocumentEvidence` and `GraphEvidence`, applies a five-chunk default limit, preserves retrieval order and provenance, and deterministically deduplicates graph edges. `PromptBuilder` renders explicit requirement, document, graph, and output-section blocks.
9. **Persistence.** PostgreSQL stores versioned `prompt_templates`, stateful `generation_requests`, and one-to-many `generation_runs`. A request can therefore record multiple provider attempts without overwriting prior evidence about failures.
10. **Generation.** `GenerationService` accepts only a `PreparedGenerationRequest`; it does not retrieve or issue Cypher. It retries transient errors with bounded exponential backoff and jitter. After retry exhaustion, only retryable failures may move to the next configured provider. Validation checks output shape and explicit source/chunk citations before status becomes `GENERATED`.
11. **API and operations.** FastAPI validates input with Pydantic, attaches an `X-Request-ID`, logs structured JSON, and maps internal errors to safe HTTP responses. Lifespan initializes the embedding model, FAISS/BM25 retrieval, Neo4j driver, engine/session factory, and provider clients once per process. `/health` is cheap liveness; `/ready` checks PostgreSQL, Neo4j, and loaded FAISS without making paid model calls.

### Business problem and OMS domain

The platform prepares grounded SDD content from enterprise knowledge. In the fictional OMS, support agents need to refund selected items from a completed order. Order Service owns eligibility and orchestration; Payment Service owns financial execution through an external Payment Gateway; Order Database stores refund/audit state; Order Service publishes `OrderItemsRefunded`; Inventory and Notification consume downstream information. Key constraints include idempotency, timeout reconciliation, per-line quantity/value limits, seven-year audit retention, and exclusion of PII/card data from logs and events.

## 2. Exact current request flow

The production FastAPI path is:

```text
POST /api/v1/generations
→ RequestContextMiddleware selects/generates X-Request-ID
→ Pydantic GenerationCreateRequest validation
→ per-request SQLAlchemy Session and repositories
→ GenerationApplicationService.generate()
→ GenerationOrchestrator.prepare_generation_context()
   → active PromptTemplate lookup
   → generation_requests row created as CREATED
   → CachedRetrievalCoordinator
   → RetrievalCoordinator mode=hybrid (FAISS + BM25 → RRF)
   → deterministic detect_known_entities()
   → GraphEnrichmentService / bounded Neo4j traversal
      or document-only GraphContext on configured Neo4j driver failure
   → ContextBuilder → GroundedContext
   → PromptBuilder → RenderedPrompt
   → request status CONTEXT_READY
→ GenerationService.generate()
   → generation_runs row created for provider attempt
   → bounded same-provider retries
   → optional next-provider fallback for retryable exhausted failure
   → LLMResponse
   → validate_generation()
   → run/request persisted GENERATED or FAILED
→ GenerationResponse, HTTP 201, X-Request-ID response header
```

Important accuracy point: the CrossEncoder is **not** wired into this FastAPI path. Its current flow is an evaluation-only mode named `hybrid_reranked`:

```text
RetrievalCoordinator hybrid top 10
→ CrossEncoderReranker
→ evaluation report
```

## 3. Ingestion interview questions

| Question | Concise answer | Deeper follow-up | Project-specific example |
|---|---|---|---|
| Why normalize text? | To make parsing and chunk IDs/rankings repeatable without destroying technical meaning. | The code normalizes CRLF/CR to LF, trims trailing whitespace, and collapses repeated blank lines. It deliberately preserves headings, lists, fenced code, routes, IDs, and error codes. | `POST /orders/{order_id}/refunds` and `IDEMPOTENCY_CONFLICT` remain unchanged. |
| Why a common `Document` representation? | It separates source-specific loading from downstream chunking and retrieval. | Future PDF, DOCX, Confluence, or SharePoint loaders can map into the same fields without changing chunking or retrieval contracts. | Markdown and text currently produce the same `Document` model with different `SourceType`. |
| Why section-aware chunking? | Enterprise documents are organized by headings, and preserving a heading with its body supplies better semantic context. | Blind fixed windows can mix unrelated API, failure, and security sections. The chunker first splits on headings outside fenced code blocks. | “Payment Gateway behavior” stays with timeout/idempotency details. |
| What is the chunk-size tradeoff? | Small chunks are focused but can lose context; large chunks preserve context but add retrieval noise and token cost. | The current 1,200-character default is explainable and suitable for tiny fixtures, not an empirically universal optimum. Production tuning would be tokenizer- and corpus-aware. | A small ownership section stays whole; only an oversized section is recursively boundary-split. |
| Why overlap? | It preserves local continuity when one oversized section must be split. | Overlap duplicates content and can create correlated retrieval results, so it is applied only to oversized sections, not every section. | Default overlap is 120 characters and must remain smaller than `max_chars`. |
| Why metadata? | It preserves provenance and enables filtering, debugging, authorization, and exact lookup. | Metadata includes source path/type, document type, title, and selected explicit fields. It is copied into every real chunk. | `OMS-REF-001` is inherited from requirement front matter and indexed lexically on the first chunk. |
| Why suppress metadata-only chunks? | A title/front-matter block is useful provenance but poor standalone retrieval evidence. | The parser recognizes only supported bold-label metadata; it avoids an overly clever front-matter language. | Requirement owner/priority remain in metadata but are not emitted as an empty semantic answer chunk. |
| How would PDF/Word ingestion differ? | Add source-specific extraction before normalization and common modeling. | PDF needs layout/OCR/table handling; DOCX needs heading/list/table relationships; both require extraction-quality diagnostics. Current code does not implement them. | A future PDF loader would still return `Document`, then reuse `chunk_document()`. |
| How would Confluence/SharePoint work? | Add authenticated connectors that fetch content, version, ACL, canonical URL, and modification metadata. | Incremental synchronization and access-control propagation matter more than HTML-to-text alone. Current reconstruction has no connector or credentials. | A Confluence page ID/version could become stable source metadata and an indexing checkpoint. |
| How would incremental indexing work? | Compare canonical source ID, version, and content hash; reprocess changed documents and remove superseded chunks. | The current implementation rebuilds from the local corpus. Production needs an index manifest and atomic version switch. | Updating `partial_refunds.md` would replace only its document/chunk rows in a versioned index. |
| How would you version documents? | Store stable source identity separately from revision/content hash. | Chunk IDs currently depend on source path and index, so editing content can keep an ID even when meaning changes. Production should include revision/index-version provenance. | `requirements/partial_refunds.md` needs a revision timestamp or source version in addition to `doc-f69…`. |
| What about duplicates? | Current code does not content-deduplicate documents. | Same path is deterministic, but identical content at two paths becomes two documents. Production can use content hashes plus canonical-source rules while preserving legitimate copies/versions. | A copied SDD in two directories could otherwise inflate BM25 and dense evidence. |
| What happens with malformed documents? | Unsupported extensions are ignored by recursive discovery; explicit unsupported loads fail, and UTF-8/read/parser errors propagate. | Production should quarantine failures, emit document-level diagnostics, retry transient connector errors, and avoid blocking unrelated documents. | A binary file renamed `.txt` could fail UTF-8 decoding; current batch has no dead-letter path. |

## 4. Embeddings and FAISS questions

### Why embeddings?

Embeddings map semantically related wording into nearby vectors, allowing paraphrase retrieval when exact terms differ. For example, “external financial provider does not respond” can retrieve payment-gateway timeout evidence.

### Why `all-MiniLM-L6-v2`?

It is a small, widely understood local Sentence Transformer suitable for CPU interview work. It balances setup cost and semantic quality. It is a reconstruction choice, not a claim about the historical enterprise model.

### What is the embedding dimension?

`384`. The service reads the dimension from the loaded model rather than hardcoding it into vector-store logic.

### What text is embedded?

`chunk_embedding_text()` includes document title, section heading when present, and full chunk text. This gives a short section body useful semantic context.

### Why L2-normalize?

Cosine similarity is the dot product divided by both vector norms. L2-normalizing corpus and query vectors to unit length makes their inner product equal cosine similarity.

### Why `IndexFlatIP`?

It performs exact inner-product search and is easy to explain and test. With only 23 chunks, approximate search adds complexity without value.

### IndexFlat versus approximate indexes

`IndexFlatIP` scans every vector and gives exact results. IVF, HNSW, or product quantization trade some recall and operational complexity for lower latency/memory at larger scale. Approximate indexes require training/tuning and measured recall checks.

### Why FAISS?

FAISS provides efficient local vector indexing, normalization utilities, persistence, and several future approximate index types without requiring a managed service for this reconstruction.

### What are FAISS limitations here?

The local index has no native document authorization, distributed coordination, transactional metadata updates, replication, or online multi-writer support. Each FastAPI process loads its own copy.

### Where is metadata stored?

Outside the FAISS binary. `chunks.faiss` stores vectors; `chunks.json` stores the embedding-model name and deterministic row-ordered `DocumentChunk` mapping. Search row IDs are converted back through that mapping.

### How is the index refreshed?

The CLI can rebuild from the current corpus and save both files. Application startup loads persisted files if present; otherwise it builds and saves them. There is no live atomic refresh endpoint yet.

### How would you scale to millions of chunks?

Benchmark approximate FAISS indexes such as IVF/HNSW/PQ, shard by tenant/domain, separate retrieval into a service, or migrate to a vector-capable managed/search system. Preserve index versioning, metadata/ACL filtering, and offline Recall@K evaluation during migration.

## 5. BM25 questions

### How does BM25 work conceptually?

BM25 rewards query terms that occur in a document, gives more weight to rare terms, saturates repeated term frequency, and normalizes for document length. `rank-bm25` implements the formula; the project keeps tokenization explicit.

### What is term-frequency saturation?

The first few occurrences help, but repeating a term many times does not increase score linearly. This reduces keyword-stuffing effects.

### What is inverse document frequency?

A term found in few chunks is more discriminative than a term present everywhere. `IDEMPOTENCY_CONFLICT` should therefore carry more lexical value than “refund.”

### Why length normalization?

Long chunks naturally contain more term matches. BM25 compensates so they do not win solely because they contain more words.

### Why does BM25 complement dense retrieval?

Dense retrieval is good at meaning and paraphrase; BM25 is strong for literal contracts and identifiers. The corpus contains many precise technical strings, so lexical retrieval is unusually effective.

### Real examples

- `IDEMPOTENCY_CONFLICT`: BM25 rank 1; dense rank 3.
- `OMS-REF-001`: BM25 rank 1; dense misses the top 10.
- API routes are retained as whole tokens and decomposed into components.

### BM25 failure cases

It can miss paraphrases with no shared vocabulary, overvalue common domain terms, and cannot understand relationship meaning. The notification query ranked first relevant evidence at 4 lexically but 2 densely.

## 6. Hybrid retrieval and RRF questions

### Why hybrid retrieval?

Enterprise queries mix semantic wording with exact codes, routes, service names, and IDs. Running dense and lexical retrieval protects against the blind spots of either alone.

### Why not add raw scores?

FAISS returns normalized inner-product similarity while BM25 returns an unbounded corpus-dependent relevance score. Their magnitudes are not calibrated. Adding them would make weighting arbitrary and unstable.

### What is Reciprocal Rank Fusion?

For each candidate:

```text
RRF(chunk) = Σ 1 / (rrf_constant + rank_from_retriever)
```

A chunk seen by both rankings receives two contributions. Candidates are deduplicated by chunk ID, while original ranks/scores remain available for debugging.

### What is the RRF constant?

The current value is `60`. A larger constant flattens differences between early ranks; a smaller one emphasizes top positions. It is configurable and should be tuned with evaluation rather than folklore.

### Benefits and limitations

RRF is simple, deterministic, and score-scale independent. It cannot understand relevance beyond input ranks, cannot recover missing candidates, and can demote a strong lexical result when dense ranking contributes noise.

### Why did hybrid not automatically beat BM25?

This tiny OMS corpus contains explicit identifiers, ownership sentences, API routes, and repeated domain vocabulary. BM25 matches those literals very well. RRF improved candidate breadth but sometimes diluted BM25’s excellent first rank with weaker dense positions.

### Actual baseline metrics

| Mode | P@1 | R@1 | Hit@1 | P@3 | R@3 | Hit@3 | P@5 | R@5 | Hit@5 | P@10 | R@10 | Hit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | .350 | .158 | .350 | .333 | .483 | .750 | .290 | .742 | .950 | .170 | .842 | .950 | .559 |
| BM25 | .700 | .442 | .700 | .433 | .700 | .900 | .320 | .833 | 1.000 | .175 | .883 | 1.000 | .825 |
| Hybrid | .550 | .308 | .550 | .450 | .683 | .850 | .310 | .783 | .950 | .190 | .942 | 1.000 | .721 |

## 7. CrossEncoder questions

### Retriever versus reranker

A retriever cheaply searches the whole corpus to maximize candidate recall. A reranker applies a more expensive query-document model to a small fixed candidate set to improve order.

### Bi-encoder versus CrossEncoder

The Sentence Transformer bi-encoder independently embeds query and chunk, enabling reusable corpus vectors and fast FAISS search. A CrossEncoder jointly attends to the query/chunk pair, usually modeling fine-grained relevance better but requiring inference for every pair at query time.

### Why candidate K=10?

The evaluation showed relevant evidence often reached the hybrid top 10 even when it ranked poorly. Reranking only five would discard ranks 6–10 before the expensive model could help.

### Why can’t reranking recover missing evidence?

It only sorts candidates passed to it. Dense misses `OMS-REF-001` in its own top 10; the reranker can act only because the hybrid candidate set still contains the BM25 result.

### Current model and integration

`cross-encoder/ms-marco-MiniLM-L-6-v2` is used on CPU. `CrossEncoderReranker` preserves dense, lexical, and RRF diagnostics and attaches a separate `reranker_score`. It is evaluation-only and disabled by default.

### Actual quality and latency

| Mode | Recall@5 | Hit@5 | Recall@10 | MRR@10 | Local average time |
|---|---:|---:|---:|---:|---:|
| Hybrid | .783 | .950 | .942 | .721 | 10.97 ms in saved comparison |
| Hybrid + CrossEncoder | .858 | 1.000 | .942 | .831 | 425.77 ms total |

The reranked run splits into about 10.56 ms candidate retrieval plus 415.18 ms CrossEncoder scoring. Local timing varied across runs: warmed hybrid was roughly 9–13 ms and total reranked retrieval roughly 418–456 ms. Therefore quote the measured order of magnitude, not false precision. The older “about 372 ms” observation is not the latest saved artifact.

### Known changes

- Gateway timeout: 5 → 3.
- `OMS-REF-001`: 7 → 5.
- Support API: 4 → 3.
- Shipping: 2 → 1.
- Transactional outbox: 2 → 1.
- Regression—notification refund inputs: 2 → 4.

### Enable or disable?

Decision B: it improves aggregate ranking but adds roughly 400 ms of CPU inference and has one observed regression. Keep it opt-in until workload latency targets, larger labels, and category analysis justify rollout.

## 8. Neo4j questions

### Why graph enrichment?

Text retrieval finds relevant passages; Neo4j returns explicitly modeled enterprise relationships useful for impact analysis. It can show that Order Service calls Payment Service, which calls Payment Gateway, without asking the LLM to infer those edges from prose.

### Graph versus vector search

Vector search ranks unstructured semantic evidence. A graph traverses named entities and typed edges. They complement rather than replace one another.

### Graph versus relational database

PostgreSQL is better for transactional workflow records and constraints. Neo4j is convenient for variable neighborhood traversal and relationship-centric questions. The current graph is small enough for either technology, but it demonstrates the boundary.

### Current ontology

All nodes share `Entity` with unique `name`, plus one type label: `System`, `Service`, `Database`, `ExternalSystem`, `Interface`, `Event`, or `Process`. The seed contains 12 entities and 20 directed relationships.

Relationship types are `CONTAINS`, `CALLS`, `WRITES_TO`, `PUBLISHES`, `CONSUMES`, `DELIVERED_VIA`, and `PARTICIPATES_IN`.

### OMS relationship examples

```text
Support Portal -[:CALLS]-> Order Service
Order Service -[:CALLS]-> Payment Service
Payment Service -[:CALLS]-> Payment Gateway
Order Service -[:WRITES_TO]-> Order Database
Order Service -[:PUBLISHES]-> OrderItemsRefunded
Inventory Service -[:CONSUMES]-> OrderItemsRefunded
Notification Service -[:CONSUMES]-> OrderItemsRefunded
OrderItemsRefunded -[:DELIVERED_VIA]-> Event Bus
```

### How does entity detection work?

It builds the complete canonical catalog from the graph seed, then scans each retrieved result’s section followed by full text using case-insensitive boundary-aware regular expressions. It returns all canonical matches, deduplicated in deterministic evidence order. It does not inspect the user query and does not use NER or an LLM.

### How do one-hop and two-hop traversal work?

`GraphRepository` repeatedly runs one parameterized direct-relationship query for each sorted frontier entity. It tracks visited names and deduplicates relationships. Default depth is 1; the safety maximum and API maximum are 3.

### Why bounded traversal?

Unbounded traversal can produce graph explosion, high latency, and irrelevant context. Small explicit depth is easier to reason about and test.

### Why doesn’t adjacency mean impact?

An edge proves a modeled relationship, not that a component must change for the current requirement. Prompt/context language treats it as relationship evidence and asks the model to label possible impact rather than claim definite modification.

### How is the graph kept consistent?

The seed uses `MERGE` and a unique `Entity.name` constraint, making repeated seeding idempotent. Production would need an authoritative source, versioned updates, deletion/tombstone handling, ownership, and consistency checks against source documents.

## 9. PostgreSQL questions

### Why PostgreSQL and Neo4j?

Neo4j stores enterprise topology; PostgreSQL stores transactional workflow and audit state. Graph adjacency and generation-run state have different consistency/query patterns.

### Current tables

- `prompt_templates`: unique `(name, version)`, system/task text, active flag, creation time.
- `generation_requests`: requirement, requested section, selected prompt-template ID, status, timestamps.
- `generation_runs`: one provider attempt with model, prompt version, output/error, latency, tokens, provider request ID, status.

### Why one request to many runs?

Retries/fallback or later regeneration can produce multiple provider attempts. Separate rows preserve history instead of overwriting a failed primary attempt with a fallback success.

### Transactions and repository boundary

Repositories contain SQLAlchemy queries and commit state changes; orchestration calls repository methods rather than embedding SQL. A stronger production design might group related status/run changes in explicit units of work to reduce partial-commit windows.

### Session lifecycle

The engine/session factory are process-wide; FastAPI yields one mutable `Session` per request and closes it afterward. Sessions are not globally shared.

### Connection pooling

PostgreSQL engines use `pool_pre_ping`, pool timeout, recycle interval, and driver connect timeout. Pool size is not specially tuned in this small project.

### Auditability and prompt versioning

Each run stores prompt name/version and provider/model/usage. Templates are immutable by conceptual version, with an active version selected by section; seeding deactivates older versions with the same name.

## 10. Grounded context and prompt questions

### Why `GroundedContext`?

It is the stable boundary between infrastructure and generation. The LLM layer receives plain document and graph evidence, not FAISS indexes, BM25 objects, or Neo4j records.

### Document versus graph evidence

`DocumentEvidence` preserves chunk ID, title, source, section, text, rank, fusion score, and metadata. `GraphEvidence` preserves source entity/type, relationship type, and target entity/type. Keeping them separate makes provenance and reasoning visible.

### Context limits

`ContextBuilder` defaults to five document chunks, preserves final retrieval order, deduplicates graph relationships, and builds ordered source provenance. It does not yet perform token counting or compression.

### Why prompt versioning?

It makes generation reproducible and auditable: a run records which template version shaped its behavior. Prompt changes can be rolled out as new versions rather than silently altering history.

### Prompt injection handling

The seeded system instruction says retrieved document/graph content is untrusted evidence/data and instructions embedded in it must be ignored. This is framing, not a complete injection classifier or security sandbox.

### Facts versus possible impacts

The prompt requires documented statements to be distinguished from possible consequences. Graph neighbors are evidence of connections, not proof that code must change.

### Insufficient evidence

The prompt explicitly requires stating what is missing. Retrieval failure stops the workflow; weak-but-nonempty retrieval does not yet have a calibrated sufficiency threshold, so human review remains important.

## 11. LLM questions

### What does the LLM do?

Only hosted text generation from an already prepared system prompt and task prompt. It does not ingest, embed, retrieve, fuse, query Neo4j, or choose prompt templates.

### What is custom?

Ingestion, normalization, chunking, metadata, embeddings, FAISS, BM25, RRF, entity matching, Neo4j enrichment, context construction, prompt persistence/rendering, validation, retries, fallback, and workflow persistence are application code.

### Why `LLMClient`?

It exposes `generate(LLMRequest) -> LLMResponse` plus provider/model properties. Higher layers never depend on OpenAI or Gemini SDK response types.

### Provider adapters

`OpenAIClient` uses the Responses API with `instructions`, `input`, bounded output tokens, reasoning effort, `store=False`, explicit timeout, and SDK retries disabled. `GeminiClient` maps the same generic request through Google’s SDK. Both map provider errors into application categories.

### Provider routing and fallback

`build_llm_clients()` creates providers in `LLM_PROVIDER_ORDER`, default `openai,gemini`. `GenerationService` persists each provider attempt. It moves to the next provider only after a retryable error exhausts same-provider retries.

### Retry versus fallback

A retry calls the same provider again after timeout, connection, rate-limit, or temporary-server failure. Fallback calls a different provider after eligible retry exhaustion. Authentication, configuration, invalid request/model, empty/invalid output, and deterministic validation do not trigger fallback.

### Backoff

The service applies bounded exponential delay plus small jitter, capped by configuration. With maximum retries 2, one provider can receive the initial attempt plus at most two retries.

### Temperature and randomness

The current adapters do **not** set a temperature. OpenAI config sets low reasoning effort, which is not the same as temperature. Even temperature zero would not guarantee identical or correct output because provider implementations and model versions can vary.

### Hallucination mitigation

Use constrained evidence, source/chunk provenance, explicit non-invention instructions, bounded context, graph facts, output validation, evaluation, and human review. No single technique proves truth.

### Model replacement

Implement the same `LLMClient` contract, add provider-specific configuration/error mapping, and preserve the rest of the RAG pipeline. A future Bedrock adapter should not move retrieval into Bedrock Knowledge Bases.

## 12. Validation questions

### Structural validation

The validator rejects empty output, text shorter than 120 characters, and obvious provider-error strings. It warns when Markdown headings exist but none clearly matches the requested section.

### Provenance validation

It extracts explicit project source paths and `doc-…-chunk-…` citations using deterministic regular expressions. Every cited path and chunk ID must appear in `GroundedContext`.

### What happens to an unknown citation?

Validation fails, the generation run is marked `FAILED`, and the request becomes `FAILED`. The error names the unknown citation internally/persistently rather than accepting it as evidence.

### Why validate source paths and chunk IDs?

Paths give human-readable provenance; chunk IDs verify the exact supplied retrieval evidence. Checking both catches invented documents and invented chunks within real documents.

### Why isn’t provenance factual correctness?

A model can cite a supplied chunk while misreading it, combining facts incorrectly, or making an unsupported uncited claim. Provenance proves availability of cited evidence, not entailment.

### Future semantic extensions

Human claim review, expected-fact coverage, claim-to-evidence entailment, contradiction detection, calibrated LLM judges, and domain-specific rules can complement deterministic checks. They require their own evaluation because automated judges are fallible.

## 13. FastAPI questions

### Why FastAPI?

It provides typed Pydantic validation, dependency injection, OpenAPI documentation, and clear synchronous route handlers with little framework ceremony.

### Route versus application service

Routes translate transport schemas and status codes. `GenerationApplicationService` coordinates the existing preparation and generation use cases. No route contains FAISS, BM25, Cypher, provider SDK, prompt rendering, or SQL.

### Request validation

`GenerationCreateRequest` forbids extra fields, strips strings, requires 1–20,000 characters, currently permits only `impact_analysis`, and bounds graph hops from 1 to 3. Invalid input receives FastAPI’s 422 response.

### Resource lifecycle

Lifespan initializes the embedding model, FAISS mapping/index, BM25, retrieval cache, Neo4j driver, database engine/session factory, and LLM clients once per process. Neo4j and the engine close at shutdown.

### Why share the embedding model/index?

Loading model weights and indexes per request would waste latency and memory. They are read-mostly process resources. Mutable SQLAlchemy sessions remain request-scoped.

### Synchronous flow

`POST /api/v1/generations` waits for preparation, provider generation, validation, and persistence, then returns 201. Queues/background jobs are not implemented.

### GET behavior

`GET /api/v1/generations/{request_id}` reads the request and latest run from PostgreSQL and never reruns generation. Unknown IDs return 404.

### Error status examples

- 201: newly generated result.
- 404: unknown generation.
- 422: Pydantic validation failure.
- 502: deterministic/non-retryable provider failure.
- 503: retryable provider or infrastructure unavailability.
- 500: safely mapped unexpected error.

## 14. Production reliability questions

### Structured logging

`JsonLogFormatter` emits timestamp, level, logger, event, correlation ID, and deliberately selected fields. Runtime stages log safe counts, timings, provider/model, fallback, token usage, and status—not full evidence or secrets.

### Correlation IDs

Middleware accepts a safe `X-Request-ID` up to 128 characters or generates a UUID. It stores the value in ASGI state and a `ContextVar`, returns it in response headers, and treats it only as untrusted observability data.

### Stage latency

The orchestrator times retrieval, graph, context/prompt, and persistence. Generation times provider and validation stages. Middleware times the total HTTP request with `perf_counter`-based helpers.

### Timeouts

Provider calls, Neo4j connection/query work, and PostgreSQL connect/pool acquisition have explicit environment-backed limits. No paid provider call occurs in readiness.

### Cache

The process-local cache stores final retrieval responses with TTL and LRU eviction. The key includes normalized query, mode, top K, corpus mapping hash, and retrieval-settings version. Values are deep-copied to avoid shared mutation.

### Cache invalidation

A changed persisted mapping produces a new corpus version after process rebuild; startup resource construction creates a fresh in-memory cache. There is no distributed invalidation or Redis.

### Liveness versus readiness

`/health` says the process can respond and performs no dependency calls. `/ready` checks PostgreSQL, Neo4j, and whether FAISS has entries; it returns 503 if any is unavailable and never calls an LLM.

### Secrets and PII-safe logging

Keys, database/Neo4j passwords, and URLs with credentials come from environment variables and `.env` is ignored. Logs avoid authorization headers, full document contents, and raw provider/database/graph exceptions in client responses.

### Rate limiting and circuit breakers

Neither is implemented. Production could enforce tenant/user quotas at an API gateway or middleware and use a circuit breaker to stop repeatedly calling a failing dependency after a measured threshold. Bounded retries are not a circuit breaker.

### Horizontal scaling

A load balancer can route to multiple FastAPI processes using shared PostgreSQL, Neo4j, and hosted providers. Each process currently loads its own Sentence Transformer, FAISS index, BM25 corpus, and local cache, trading memory for local latency.

## 15. Failure scenarios

| Scenario | Current expected behavior | Rationale | Production improvement |
|---|---|---|---|
| Neo4j is down | When graph degradation is enabled and a Neo4j driver error occurs, document retrieval remains, graph evidence becomes empty, a warning is attached, and context reaches `CONTEXT_READY`; generation can continue document-only. | Graph evidence is additive; fabricated edges are worse than explicitly missing graph context. | Tighten driver retry budget, add circuit breaker/metrics, and decide which sections require graph evidence. |
| PostgreSQL is down | Fail the request with safe infrastructure mapping; do not generate without traceable workflow persistence. | Request/run auditability is required. | Highly available PostgreSQL, transaction retry for safe operations, backups, and health-based traffic removal. |
| FAISS index is missing | Startup builds it from current documents and persists it; startup fails if ingestion/model/index creation fails. | Local recovery is reasonable for the tiny corpus. | Build versioned indexes asynchronously, validate, then atomically switch. |
| Embedding model unavailable | Resource startup/index build fails; readiness should not advertise a usable application. | Semantic query embedding is essential to the configured hybrid path. | Prepackage/cache approved model artifacts, checksum them, and provide controlled rollout/rollback. |
| Primary LLM unavailable | Retry only transient failures, then use the next configured provider if eligible. | Bounded retry handles transient faults; fallback reduces provider dependency. | Per-provider SLOs, circuit breaker, cost policy, and capability-compatible prompts. |
| All LLMs unavailable | Mark attempts/request failed and return a safe error. | Do not invent output or report success. | Queue an explicitly asynchronous retry only if product semantics allow it. |
| Retrieval returns no relevant evidence | There is no calibrated relevance threshold today; nearest results may still exist. Prompt says to report insufficiency, but retrieval failure itself stops generation. | Avoid silently sending wholly ungrounded prompts, while acknowledging current sufficiency limits. | Add calibrated sufficiency/abstention policy using evaluation data. |
| LLM cites an invalid chunk | Provenance validation fails and persistence records `FAILED`. | Explicit citations must belong to supplied context. | Add claim-level entailment and reviewer workflow. |
| Duplicate API request | Each POST currently creates a new generation request; API idempotency is not implemented. | The reconstruction focused on RAG flow, not client deduplication. | Require idempotency key scoped to caller/tenant and enforce a unique database constraint. |
| Stale retrieval cache | TTL eventually expires; corpus/settings versions separate new keys after resource rebuild. | Simple local cache avoids distributed complexity. | Publish index-version invalidations or use distributed version-aware cache. |
| Prompt is too large | Context has a five-document limit and provider output is bounded, but there is no tokenizer-based input budget. | Deliberate evidence limits are safer than unlimited context. | Token budgeting, evidence compression, section-specific allocation, and fail-before-provider checks. |
| Graph explosion | API and repository enforce 1–3 hops; traversal deduplicates visited entities/edges. | Bounded exploration prevents unbounded neighborhoods. | Degree caps, relationship allowlists, budgets, and query profiling. |
| Domain documents conflict | Current retrieval can supply both; prompt should distinguish documented facts and uncertainty, but no authority resolver exists. | Hiding conflict would be misleading. | Source authority, effective dates, version precedence, contradiction detection, and human resolution. |

The Neo4j degradation unit test specifically proves: document retrieval returns one chunk, graph enrichment raises `ServiceUnavailable`, graph evidence becomes zero, an operational warning is present, and preparation still reaches `CONTEXT_READY`. The code then permits normal generation. The test itself stops at `CONTEXT_READY`; it does not make a provider call, so describe “generation can continue,” not “the unit test called an LLM.” Manual failure work also identified excessive Neo4j driver retry time as a remaining optimization; query timeout exists, but a tighter transaction retry budget/circuit breaker would improve degradation latency.

## 16. Retrieval evaluation questions

### What is the gold dataset?

`oms_rag_gold.json` contains 20 manually labeled queries against the fixed 23-chunk OMS corpus. Each has category and exact relevant chunk IDs. Loading rejects IDs absent from a fresh ingestion result.

### Metric definitions

- `Precision@K = relevant items retrieved in top K / K`.
- `Recall@K = relevant items retrieved in top K / all labeled relevant items`.
- `Hit@K = 1` if any relevant result appears in top K, otherwise 0.
- `Reciprocal Rank = 1 / first relevant rank`, or zero for a miss.
- `MRR = mean Reciprocal Rank` across cases.

### Why K=1/3/5/10?

K=1 measures immediate usefulness; K=3/5 approximate practical context budgets; K=10 measures candidate recall for reranking. Higher K can improve recall but reduce precision and increase context/token cost.

### Why category-level analysis?

Aggregates can hide that BM25 excels on identifiers while dense retrieval helps paraphrases. Per-query diagnostics identify candidate misses, low ranks, hybrid improvements/harm, and reranker regressions.

### Why did high Recall@10 plus weaker MRR suggest reranking?

Hybrid usually contained relevant evidence in the top 10 (`Recall@10=.942`, `Hit@10=1.0`) but often placed it too low (`MRR=.721`). A reranker can improve order only when candidate recall is already strong.

### What does the dataset prove?

It provides a repeatable baseline for this corpus and exposes real failures. It does not prove general production quality: 20 hand-labeled queries have sampling and labeling limitations.

## 17. Generation evaluation

Ten generation cases are prepared separately from retrieval labels. They cover API, ownership, eligibility, shipping, timeout reconciliation, event behavior, inventory inputs, audit retention, PII/card constraints, and cancellation differences.

Only one saved generation result is currently evaluated. Its individual structural/provenance/fact-coverage/relevance checks pass, but it is incorrect to say overall generation quality is 100%.

Dimensions remain separate:

- **Structural validity:** nonempty/useful-length output and no obvious provider error.
- **Provenance validity:** explicit cited paths/chunk IDs belong to supplied evidence.
- **Expected-fact coverage:** transparent manually curated term groups, with covered and uncovered facts shown.
- **Human relevance:** `pass`, `fail`, or `not_reviewed`.
- **Unsupported claims:** reviewer notes and an explicit review-complete flag.
- **Latency and tokens:** provider latency plus optional input/output usage.

The report honestly prints `10 prepared, 1 evaluated, 9 missing`. No paid provider is called by the evaluator or normal tests.

## 18. Scaling and system-design questions

| Question | Current implementation | Future production design |
|---|---|---|
| One million documents | Exact local `IndexFlatIP`; unsuitable for this volume. | Approximate index or managed vector/search service, sharding, metadata filters, batch ingestion, measured recall. |
| Ten million documents | No distributed FAISS management. | Dedicated retrieval tier, distributed shards/replicas, IVF/PQ/HNSW or managed system, capacity and failover planning. |
| Frequent updates | Full local rebuild/manual refresh. | Event-driven incremental parsing, versioned manifests, tombstones, background builds, atomic alias/version switch. |
| Distributed FAISS | One index per FastAPI process. | Separate retrieval service with controlled index versions, or a managed distributed vector store. |
| Managed vector migration | Not implemented. | Dual-write/build, shadow queries, compare Recall@K/latency, switch behind interface, preserve rollback. |
| OpenSearch | Not used. | Candidate for BM25 plus vector/hybrid retrieval and metadata filtering; validate score fusion and operational costs. |
| Separate retrieval service | In-process coordinator. | Useful when model/index memory, independent scaling, and index rollout justify network overhead. |
| Message queues | None. | Use for ingestion/index builds and possibly async generation—not necessary for cheap reads. |
| Asynchronous ingestion | Local synchronous CLI. | S3/connector event → queue/workflow → parse/chunk/embed → versioned index build → publish. |
| Multi-tenancy | Not implemented. | Tenant identity in every document/chunk, storage partition/filter, per-tenant keys/quotas, authorization before retrieval. |
| Access-control filtering | Not implemented. | Propagate source ACLs, enforce allowed document IDs/tenant filters before candidate exposure, and recheck before prompting. |
| Document-level authorization | No auth/RBAC. | Policy service or database-backed ACL filter applied to lexical/vector search, never post-generation only. |
| Disaster recovery | Local artifacts plus shared DB/graph instructions. | Database backups/PITR, graph backup, source-of-truth replay, versioned index artifacts, tested restore runbooks. |
| Index versioning | Mapping hash versions cache keys; mapping has format/model metadata. | Immutable corpus/index versions, build status, compatibility checks, atomic promotion and rollback. |

## 19. AWS questions

### Is AWS deployed in this reconstruction?

No. There is no Lambda deployment, Bedrock Runtime adapter, Bedrock Knowledge Base, Kendra client, S3 event integration, or boto3 dependency in the current retrieval/generation path.

### Plausible future Lambda role

```text
S3 document upload
→ event source
→ Lambda ingestion trigger
→ durable workflow/queue
→ parsing, chunking, embedding, index refresh
```

Lambda is better as a short event trigger than the main synchronous API because the current path benefits from warm model/index memory, persistent connections, and long provider latency.

### Plausible Bedrock role

A future `BedrockLLMClient` could implement the same provider-independent interface for hosted generation or provider fallback. Bedrock should not replace the custom retrieval pipeline unless the architecture is intentionally redesigned and reevaluated.

## 20. Historical versus reconstructed project

### Historical/resume story

The original project description referenced AWS Kendra, LLaMA-13B, and Lambda. Those references should be discussed as components of the historical architecture or team context only to the extent personally verified.

### Current hands-on reconstruction

This repository implements custom ingestion, Markdown/text parsing, section chunking, MiniLM embeddings, FAISS, BM25, RRF, optional CrossEncoder evaluation, deterministic entity matching, Neo4j, PostgreSQL, prompt orchestration, OpenAI/Gemini adapters, validation, FastAPI, operational hardening, and offline evaluation.

### Safe interview wording

“The original system used managed retrieval components in parts of its architecture and referenced Kendra, LLaMA-13B, and Lambda. To rebuild the workflow and deeply understand the retrieval tradeoffs, I later implemented this hands-on reconstruction using FAISS, BM25, RRF, Neo4j, PostgreSQL, hosted-provider adapters, and an offline evaluation set. I keep the historical system and my reconstruction separate when describing production claims.”

Avoid: “I replaced production Kendra with FAISS and improved production MRR,” unless that actually happened and can be evidenced. Prefer: “In my reconstruction, the measured BM25/hybrid/reranker results were…”

## 21. Ownership questions

### What did you personally design?

“In the reconstruction I designed the layer boundaries, deterministic ingestion/chunking, dense-plus-lexical retrieval, rank fusion observability, graph enrichment boundary, grounded context, versioned prompt persistence, provider abstraction, validation, API lifecycle, and evaluation framework. For historical claims, I state only the components I personally worked on.”

### What was hardest?

“The hardest part was not calling an LLM; it was making evidence movement explicit and measurable. Dense retrieval handled paraphrases but ranked exact APIs/IDs poorly, while BM25 was unexpectedly strong. The evaluation forced an honest reranker decision.”

### What tradeoff did you make?

“I chose exact FAISS and deterministic parsers for explainability on a tiny corpus, then isolated scale-oriented alternatives. I also kept graph degradation document-only but made database/retrieval failures fatal.”

### What failed?

“Hybrid RRF did not automatically beat BM25 MRR. `OMS-REF-001` was rank 1 lexically but rank 7 after fusion, and the CrossEncoder later regressed notification evidence from rank 2 to 4.”

### What would you change?

“First I would strengthen the labeled dataset and add access-control-aware retrieval. Then I would calibrate fusion/reranking by query category, implement versioned incremental indexing, token budgets, and tighter Neo4j retry/degradation latency.”

### What metrics improved?

“In the reconstruction, optional reranking improved hybrid MRR@10 from .721 to .831, Recall@5 from .783 to .858, and Hit@5 from .950 to 1.0, at about 415 ms extra reranker time.”

### What did you learn?

“Hybrid is a candidate-recall strategy, not a guarantee of top-rank quality. Provenance is not factual correctness, retries are not fallback, and graph adjacency is not impact.”

### Which component would you redesign first?

“For production, ingestion/index lifecycle and authorization. Correct incremental updates, versioning, deletion, tenant isolation, and ACL filtering are prerequisites before scaling model sophistication.”

### What did you not implement?

“No PDF/DOCX/connectors, managed retrieval, Bedrock/Kendra, AWS deployment, auth/RBAC, distributed cache, async workers, semantic entailment validator, or production reranker rollout.”

### What would you own in production?

“I would own the evidence contract and retrieval-quality lifecycle: source/version/ACL ingestion, index releases, gold labels, failure analysis, context provenance, and safe rollout metrics across retrieval and generation.”

## 22. Top 20 likely interview questions

1. **Walk me through the system.** Use the two-minute answer and name the boundaries.
2. **Why combine FAISS and BM25?** Semantic paraphrases plus exact enterprise identifiers.
3. **Why RRF?** Rank fusion avoids incompatible raw score scales.
4. **Why was BM25 MRR better?** Small explicit technical corpus favored exact matching.
5. **What did reranking improve?** MRR .721→.831 and Hit@5 .950→1.0, with latency cost.
6. **Why not enable the reranker?** About 415 ms added CPU time and one regression.
7. **How is cosine implemented?** L2-normalize both sides, then `IndexFlatIP`.
8. **How do chunks preserve meaning?** Heading-aware sections; overlap only on oversized sections.
9. **How do you map FAISS rows back?** Separate ordered chunk mapping JSON.
10. **Why Neo4j?** Explicit typed relationship evidence for bounded impact exploration.
11. **How are graph entities found?** Case-insensitive canonical-name scan over retrieved headings/text.
12. **Why PostgreSQL too?** Transactional prompts/request/run state, not topology traversal.
13. **What is `GroundedContext`?** Stable generation boundary separating document and graph evidence.
14. **How do retries differ from fallback?** Same provider versus next provider.
15. **How do you prevent hallucinations?** Evidence limits, prompt rules, provenance validation, evaluation, human review—not a guarantee.
16. **What happens when Neo4j fails?** Configured document-only degradation with warning.
17. **How are expensive resources managed?** Once per FastAPI process; DB sessions per request.
18. **What metrics do you use?** P/R/Hit at 1/3/5/10 plus MRR and per-query failures.
19. **How would this scale?** Versioned async ingestion and a dedicated/distributed retrieval tier with ACLs.
20. **What is historical versus reconstructed?** Managed historical references are clearly separated from hands-on custom implementation.

## 23. Rapid-fire questions and concise answers

1. **What is RAG?** Retrieve evidence and condition generation on it.
2. **What is the corpus size?** Four documents producing 23 retrievable chunks.
3. **Current source types?** UTF-8 Markdown and text.
4. **Default chunk size?** 1,200 characters.
5. **Default overlap?** 120 characters, only for oversized sections.
6. **How are document IDs formed?** SHA-256 of relative source path, truncated to 12 hex characters.
7. **How are chunk IDs formed?** Document ID plus zero-padded source-order chunk index.
8. **Why no metadata-only chunks?** Metadata is provenance, not useful standalone semantic evidence.
9. **Embedding model?** `sentence-transformers/all-MiniLM-L6-v2`.
10. **Embedding dimension?** 384.
11. **Dense similarity?** Cosine via normalized inner product.
12. **FAISS index type?** Exact `IndexFlatIP`.
13. **Does FAISS store metadata?** No; a separate ordered JSON mapping does.
14. **BM25 library?** `rank-bm25` / `BM25Okapi`.
15. **Does tokenization stem words?** No; it uses simple regex tokens and technical components.
16. **Example exact identifier?** `IDEMPOTENCY_CONFLICT`.
17. **Example requirement ID?** `OMS-REF-001`.
18. **Default retrieval mode?** Hybrid.
19. **Dense candidate count?** 10.
20. **Lexical candidate count?** 10.
21. **Default final top K?** 5.
22. **RRF constant?** 60.
23. **Why not add raw scores?** Different, uncalibrated scales.
24. **CrossEncoder model?** `ms-marco-MiniLM-L-6-v2`.
25. **Is reranking in the API?** No; it is an opt-in evaluation mode.
26. **Reranker candidate K?** 10.
27. **Can reranking create evidence?** No; it only reorders candidates.
28. **Graph entity count?** 12.
29. **Graph relationship count?** 20.
30. **Graph default depth?** 1.
31. **Maximum graph depth?** 3.
32. **Entity extraction model?** None; deterministic canonical-name regex matching.
33. **Does it scan the query?** No; it scans retrieved section/text evidence.
34. **Graph uniqueness?** Shared `Entity.name` uniqueness constraint.
35. **What does adjacency prove?** A modeled relationship, not definite impact.
36. **PostgreSQL tables?** `prompt_templates`, `generation_requests`, `generation_runs`.
37. **Why multiple runs?** Preserve provider attempt/fallback history.
38. **Current prompt section?** `impact_analysis`.
39. **Default context chunks?** 5.
40. **Does context count tokens?** Not yet.
41. **LLM providers?** OpenAI and Gemini adapters.
42. **Does OpenAI retrieve?** No; it generates only.
43. **Do SDKs retry internally?** OpenAI SDK retries are disabled; application policy owns retries.
44. **What failures retry?** Timeout, connection, rate limit, temporary server errors.
45. **Does auth failure fallback?** No; it is deterministic/non-retryable.
46. **Is temperature configured?** No.
47. **Minimum output length?** 120 characters.
48. **What citations are checked?** Explicit project source paths and chunk IDs.
49. **Does validation prove truth?** No.
50. **POST endpoint?** `/api/v1/generations`.
51. **GET endpoint?** `/api/v1/generations/{request_id}`.
52. **Liveness endpoint?** `/health`.
53. **Readiness endpoint?** `/ready`.
54. **Does readiness call an LLM?** No.
55. **Request ID header?** `X-Request-ID`.
56. **Cache type?** Process-local TTL/LRU.
57. **Default cache TTL?** 300 seconds.
58. **Default cache entries?** 128.
59. **Does cache store LLM output?** No.
60. **Retrieval gold cases?** 20.
61. **Generation cases prepared?** 10.
62. **Generation captures evaluated?** 1 currently.
63. **Best baseline MRR?** BM25 at .825.
64. **Hybrid Recall@10?** .942.
65. **Hybrid Hit@10?** 1.0.
66. **Reranked MRR?** .831.
67. **Current AWS deployment?** None.
68. **Current Kendra/Bedrock KB use?** None.
69. **Plausible Lambda role?** Trigger asynchronous ingestion/index refresh from S3 events.
70. **Normal tests call paid APIs?** No; live integrations are explicitly gated.

## 24. Hard and trick questions

### 1. Why use hybrid if BM25 had higher MRR?

BM25 was strongest on this small literal-heavy dataset, but hybrid had the best Recall@10 (.942) and perfect Hit@10. Hybrid is useful as a candidate-recall layer for varied future queries; the honest conclusion is not that hybrid wins every metric. I would keep lexical mode observable and consider query-aware routing/fusion only after more labels.

### 2. Why not use BM25 only?

Dense retrieval beat BM25 on some semantic cases, such as notification evidence and service-flow phrasing, and future enterprise wording may be less literal. BM25-only would trade away paraphrase robustness based on a tiny current sample.

### 3. Why not Elasticsearch/OpenSearch?

It would be reasonable in production for distributed BM25, vector search, filters, and operations. The reconstruction uses FAISS plus `rank-bm25` to expose mechanics line by line. Migration should be justified with scale/ACL/operations and evaluated against the same labels.

### 4. Why Neo4j instead of putting relationships in chunk metadata?

Metadata works for local attributes and simple filters. Neo4j supports reusable typed relationships and bounded multi-hop traversal across entities independent of whichever chunk was retrieved. For only 12 nodes it is intentionally educational; production adoption depends on graph-query value.

### 5. Why not LLM-based entity extraction?

The current known catalog is tiny and exact, so deterministic matching is cheaper, repeatable, debuggable, and has no model cost. An NER/linking model becomes useful with aliases, ambiguous names, and open-world entities, but needs labels and confidence handling.

### 6. Why should anyone trust 20 queries?

They should trust them as a transparent regression baseline, not as statistical proof. The labels are human-readable and tied to exact chunks. Production needs more reviewers, inter-annotator checks, query logs, category balance, and temporal holdouts.

### 7. Why trust your relevance labels?

Every ID resolves against the current corpus, notes explain intent, and multi-evidence cases allow multiple relevant chunks. Labels can still be incomplete; review by domain experts and disagreement tracking are required.

### 8. Why is provenance not faithfulness?

A cited source may be supplied while the generated claim contradicts or overgeneralizes it. Provenance checks membership; faithfulness requires entailment between claim and evidence.

### 9. What if gold labels are incomplete?

Precision can unfairly penalize genuinely useful unlabeled results and MRR can misrepresent first relevance. Review high-ranked “false positives,” allow graded relevance if justified, and version label corrections rather than tuning blindly.

### 10. Why rerank if it adds about 400 ms?

Only if the quality gain matters to the product latency budget. Here MRR and Hit@5 improve, but the CPU cost and one regression support keeping it off by default. Batch/model quantization or a smaller reranker could be evaluated later.

### 11. Why not replace all retrieval with a CrossEncoder?

A CrossEncoder must score every query-document pair and cannot precompute independent corpus vectors. It is viable for tens of candidates, not millions of chunks without a candidate retriever.

### 12. Why isn’t Hit@5 enough?

Hit@5 ignores how many relevant chunks are found, where the first appears within five, and how much noise is included. Recall, Precision, and MRR expose those differences.

### 13. How would access control work before retrieval?

Propagate tenant and source ACL metadata during ingestion, resolve caller permissions, and restrict the eligible corpus inside lexical/vector candidate retrieval. Filtering after retrieval or only in the prompt risks leakage through scores, logs, and generated text.

### 14. What happens when documents disagree?

Today both may be retrieved and the prompt asks for documented facts/insufficiency; no conflict engine exists. Production should preserve source authority, effective date/version, detect contradictions, and require human resolution for material conflicts.

### 15. How do you prevent tenant leakage?

The current project does not. Production requires tenant-scoped storage/index partitions or mandatory filters, authorization before retrieval, tenant-aware cache keys, separate encryption/audit, and tests proving cross-tenant denial.

### 16. Isn’t this overengineered for 23 chunks?

Yes, if the only goal were answering these fixtures. The point is to demonstrate enterprise boundaries and measure tradeoffs in a small explainable system. I would not deploy every component for a 23-chunk product without scale/relationship/audit requirements.

### 17. Why synchronous LLM generation?

It keeps Day-8 behavior simple and supports immediate responses. For long-running/high-volume work, asynchronous jobs with polling/webhooks and idempotency would improve resilience and resource usage.

### 18. Why not Lambda for the API?

The current process benefits from warm Sentence Transformer/index memory and persistent database/graph/provider clients. Lambda cold starts and long synchronous generation make it a better ingestion trigger than the main API here.

### 19. How would you roll out a new embedding model?

Build a separate full index with model/version metadata, evaluate old versus new on fixed labels, shadow representative traffic, ensure query/index model compatibility, atomically switch an alias/config, and retain rollback artifacts.

### 20. How would you migrate an existing FAISS index?

Treat it as an immutable versioned artifact. Re-embed into the target layout, validate row mapping/count/dimension, compare retrieval metrics, deploy side by side, switch readers atomically, and never mix vectors from incompatible models.

### 21. What if Neo4j degradation takes 30 seconds?

Document-only fallback is logically correct but operationally poor if driver retries consume the request budget first. Configure a tighter Neo4j retry budget, use deadline propagation/circuit breaking, measure degraded latency, and decide whether graph work should be optional or precomputed.

### 22. Why no similarity threshold?

Raw dense thresholds are model/corpus/query dependent and BM25/RRF scores are differently scaled. The project first measured score/rank behavior. A sufficiency threshold should be calibrated against labeled in-domain and out-of-domain queries, not chosen arbitrarily.

### 23. What if an LLM makes an unsupported claim without a citation?

Current provenance regex will not catch it. Human unsupported-claim review and expected-fact coverage help, but claim segmentation plus evidence-entailment checks would be the next semantic validation layer.

### 24. Why does the API create a request before retrieval?

It preserves traceability for failures and enables explicit `CREATED → CONTEXT_READY/FAILED` state. The tradeoff is durable failed records and multiple commits; production may use a unit of work while retaining failure audit.

### 25. Could provider fallback change answer quality?

Yes. Models differ in instructions, output style, latency, token accounting, and behavior. Fallback improves availability, not equivalence. Store provider/model/prompt version and evaluate providers separately.

## 25. Metrics to memorize

```text
Corpus: 4 documents, 23 chunks
Retrieval gold cases: 20
Generation cases: 10 prepared, 1 result evaluated

Dense MRR@10: 0.559
BM25 MRR@10: 0.825
Hybrid MRR@10: 0.721

Hybrid Recall@5: 0.783
Hybrid Hit@5: 0.950
Hybrid Recall@10: 0.942
Hybrid Hit@10: 1.000

Reranked Recall@5: 0.858
Reranked Hit@5: 1.000
Reranked MRR@10: 0.831

Latest saved comparison:
Hybrid: 10.97 ms average
Reranked candidate retrieval: 10.56 ms
CrossEncoder scoring: 415.18 ms
Reranked total: 425.77 ms

Embedding dimension: 384
Chunk size/overlap: 1,200 / 120 characters
Dense/lexical candidate pools: 10 / 10
Default final top K: 5
RRF constant: 60
Graph: 12 entities, 20 relationships, max 3 hops
Context limit: 5 document chunks
```

## 26. Day-12 boundary

Day 11 adds documentation only. A future Day 12 should test live interview performance rather than add architecture: timed explanations, whiteboard reconstruction, failure-scenario drills, metric recall, historical-versus-reconstructed phrasing, and adversarial follow-up questions. It should not silently change retrieval or claim unmeasured improvements.
