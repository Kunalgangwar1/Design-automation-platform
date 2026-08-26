# Interview Notes

## Business Problem


## Architecture

Historical enterprise design: FAISS was the primary semantic path and AWS Kendra was the managed enterprise-search fallback.

Current reconstruction: FAISS remains primary. A visible sufficiency policy returns FAISS results when the top score meets the configured threshold; no FAISS results or a score below the threshold invokes Amazon Bedrock Knowledge Bases `Retrieve`. Both paths return the same `RetrievalResult` shape, but provider scores are not combined.

Enterprise sources use Bedrock Knowledge Base managed ingestion/indexing. The curated local fixture corpus follows the custom Day-2 ingestion and Day-3 embedding path into FAISS. A query checks FAISS first, optionally falls back to Bedrock, then will later flow through Neo4j enrichment, custom prompt construction, and LLM generation.

Use `Retrieve`, not `RetrieveAndGenerate`, so future graph enrichment and prompt orchestration remain application-controlled.

## RAG


## FAISS

Current primary retriever: normalized 384-dimensional MiniLM embeddings with exact `IndexFlatIP` search. On unit vectors, inner product equals cosine similarity. The fallback threshold must be calibrated empirically.

## Kendra

Historical architecture: AWS Kendra was the fallback enterprise-search layer in the original project.

Current reconstruction: Kendra is not implemented. AWS placed Kendra in maintenance mode in 2026 and no longer accepts new customers, so Bedrock Knowledge Bases is used for the current managed fallback. This is an architecture evolution, not a claim that the original project used Bedrock.

BM25/RRF remains an optional local diagnostic and must not be described as equivalent to Kendra.

## Neo4j


## PostgreSQL


## FastAPI


## Lambda


## LLM


## Production Considerations

Calibrate the FAISS threshold against labeled queries; monitor fallback rate, latency, cost, authorization behavior, provider errors, and source traceability. Use the normal AWS credential provider chain and least-privilege IAM. Do not log credentials or sensitive retrieved content.

## Interview Questions
