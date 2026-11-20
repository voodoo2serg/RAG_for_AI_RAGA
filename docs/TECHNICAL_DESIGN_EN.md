# RAGA technical design: context economy, provenance and portable memory

**Edition:** Community Core · **Reviewed:** 20 November 2026 · **Scope:** public code and the design lineage documented in the earlier private project.

## 1. What problem does RAGA solve?

AI model context is transient and expensive to fill repeatedly. Long chats mix actual decisions with drafts, mistakes, repeated instructions, discussions and irrelevant reasoning. Moving to another provider or coding agent usually means rebuilding the same working state.

RAGA separates **durable project memory** from **temporary inference context**. Keep the complete original material in a local store, extract the few pieces that matter to work, retrieve only relevant evidence when needed, and retain a route back to the original transcript.

> **Design principle: keep the knowledge corpus large, but the model's immediate context small. Never substitute a compact summary for the complete original source when auditing a decision.**

This is a design objective, **not** a measured token-saving claim. The public implementation currently limits the number and size of passages used for optional local model answers; it does not have a formal token-budget optimizer or an adaptive multi-step disclosure protocol.

## 2. Reference solutions and lessons

The original project's product research compared **Dify, AnythingLLM, RAGFlow, Khoj, Open WebUI**, and developer frameworks **LangChain, LlamaIndex and Haystack**. These were architectural/product benchmarks, not dependencies, source-code donors or proof of direct code reuse.

| Reference | Evaluated strength | Implication for RAGA |
| --- | --- | --- |
| Dify | Visual AI application workflows and integrations | Do not compete solely as another app builder; provide reusable context to external tools |
| AnythingLLM | Self-hosted knowledge chat and accessible document workflows | Simple local setup matters |
| RAGFlow | Complex-document ingestion and traceable answers | Preserve identifiable source passages; richer document parsing is future work |
| Khoj | Personal knowledge and cross-tool access | Make project context independent of a single chatbot |
| Open WebUI | Local-model access and approachable interface | Local Ollama integration, without requiring a cloud model |
| LangChain, LlamaIndex, Haystack | Composable retrieval/agent building blocks | Expose an API that other tools can use rather than binding to one framework |

These comparisons are documented in the original project's product-research notes, dated 28 April 2026. They should not be interpreted as current benchmarks or claims of superiority.

**RAGA's intended differentiation** is continuity of *work*, not only question-answering over documents: conversation history becomes reviewable project state, decisions, tasks and manually controlled skills.

## 3. Internal architecture of the public edition

```text
Markdown / TXT / JSON / JSONL / CSV / LOG / ZIP
                 |
       API import + source retention
                 |
      project -> session -> chunks
                 |             |
    candidate rules, skills   lexical BM25-style retrieval
    decisions and tickets     optional local embeddings
                 |             |
            human review     ranked evidence + source IDs
                 |             |
          active project state
                 |
    continuity Markdown / full JSON backup
                 |
       external agent or local model
```

**Runtime:** Python + FastAPI, SQLite, plain-language web interface, optional Ollama. Unlike the older multi-service architecture (Next.js, PostgreSQL/pgvector, Redis/Celery, object storage), this distribution deliberately uses a small local runtime. That older stack is an architectural predecessor, not the public edition's deployment requirement.

### Component map

| File | Responsibility |
| --- | --- |
| `backend/raga_community/api.py` | Projects, sessions, manual CRUD, continuity Markdown, backup and restore |
| `backend/raga_community/knowledge.py` | Text/ZIP import, chunking, BM25-style ranker, evidence retrieval, optional grounded local-model answer |
| `backend/raga_community/semantic.py` | Ollama embedding requests, cosine similarity and optional hybrid ranking |
| `backend/raga_community/extract.py` | Transparent keyword-based extraction into draft rules, tickets, decisions and skills |
| `backend/raga_community/webui.html` | Simple personal interface |
| `backend/tests/` | Unit and API tests, including mocked semantic retrieval |
| `docker-compose.yml` | Local-only API with optional Ollama profile |

The database stores `projects`, `sessions` (original material), `items` (work objects), `chunks` (indexed passages) and optional `chunk_vectors`. Each chunk retains a `session_id`, a `chunk_index` and a source label.

## 4. Ingestion: what is processed?

The public code accepts direct text sessions and UTF-8 `.txt`, `.md`, `.json`, `.jsonl`, `.csv`, `.log` files, plus ZIP archives containing supported text files. A JSON importer traverses nested objects to find string `content`, `text` and `message` values. Import is synchronous and limited in size. Original text remains recoverable.

**Not shipped:** ready-made live connectors to specific chat providers, social services, remote coding agents or repositories; PDF/DOCX parsing; automatic event streaming; background queues. Any provider may integrate via the HTTP API if its export format is converted to accepted input.

The earlier architecture proposed two ingestion paths: *historical import* for existing transcripts, and *live events* from agents. Only the first and simple API submission exist in the public edition.

## 5. How indexing and vectorization work

### 5.1 Source chunking

`knowledge.py` splits text on paragraphs, subdivides long passages and retains limited overlapping context. The default target is roughly **1,100 characters per chunk** with **150 characters of overlap**. These are **characters, not model tokens**; passage sizes may exceed targets around paragraph boundaries. The indexed unit has an ID and a pointer to its originating session.

### 5.2 Default lexical retrieval

A BM25-like score is calculated over source chunks using term frequency, document frequency and length normalization. The public implementation scans candidate chunks within the selected project and returns a limited, ranked set, normally **eight** passages. Each result includes source, session ID, chunk ID and index for traceability.

### 5.3 Optional semantic vectors

An optional local Ollama embedding model (default example: `nomic-embed-text`) maps text into numerical vectors. The public implementation serializes those vectors to JSON in SQLite. At query time it embeds the question and computes **cosine similarity** to stored chunk vectors.

The optional hybrid mode combines a nonnegative semantic score and normalized lexical score using **0.7 semantic + 0.3 lexical**. This simple blend is not calibrated for all corpora and has no ANN index; it is intended for relatively small personal datasets. Texts are not vectorized automatically on import: call the `/vectorize` API explicitly after enabling Ollama.

**Boundary:** the private research extension **RAGA-64** is not included. This code uses ordinary embedding/cosine operations, not any proprietary optimization procedure.

## 6. Minimal context, full path on demand

It is helpful to distinguish three levels of disclosure.

| Level | What the consumer needs | Present implementation |
| --- | --- | --- |
| **A · Working state** | Current rules, decisions, unfinished tickets and chosen skills | `GET /api/projects/{id}/continuity` returns concise Markdown from active project items; currently includes up to 50 titles per category |
| **B · Supporting evidence** | The passages relevant to a specific question | `POST /api/projects/{id}/retrieve` or `/semantic-search` returns ranked source chunks with IDs and scores |
| **C · Complete original** | Full text and project history when uncertainty or audit requires it | `GET /api/projects/{id}/sessions/{session_id}` and `/backup` preserve original conversation text and items |

This is an **available manual progression**, not yet an automatic staged retrieval controller. An integrated agent can start with A, request B for a claim, and request C when it must verify the underlying record. Full backups include private conversation material and must be protected accordingly.

For optional local-model generation, `/ask` provides at most the configured number of passages, each truncated to 1,400 characters in the constructed prompt. The model is instructed to cite numbered passages and acknowledge insufficient evidence. This reduces indiscriminate prompt loading; it does **not** guarantee a fixed token budget, factual correctness or full-source visibility within a single model answer.

### Example

A long chat determines that documentation must be checked before a release.

- **Working context:** “Rule: verify citations before publication.”
- **Evidence request:** retrieve the passage in which the team made this decision.
- **Audit:** open the original session for its surrounding discussion.

The full chat stays stored while only the rule or a few supporting passages enter the immediate working context.

## 7. Knowledge governance and humanities workflows

Unlike an opaque chat memory, RAGA makes a rule, task or skill a visible item. Users may **create, edit, change status and delete** these records. The extractor only suggests candidates based on keywords; a detected phrase is not an approved fact. Skills can be exported as `SKILL.md`-shaped Markdown, but there is not yet a full skill execution engine.

This human-review principle matters to editorial, archival, research and other humanities work: preserving source language, distinguishing claims from decisions, and correcting automated interpretations are often more important than fully automatic actions.

The earlier design contemplated five knowledge levels (secrets, rules, skills, conversations, technical history), provenance links, review queues, scheduled agent reminders and context-loss tests. **Those were reference architecture concepts, not all public features.** The public database currently exposes work-item types and session links without fully implementing those five permission tiers.

## 8. External services and interoperability

The original research considered Telegram/chat imports, GitHub source collection, OpenAI-compatible models, vector stores and object storage. For the public edition the actual supported boundaries are much narrower:

- **Input:** local files and HTTP API submission; external integrations are built by API clients.
- **AI model:** optional local **Ollama** for answering and embeddings.
- **Data:** SQLite files, JSON backup/restore, Markdown continuity/skill export.
- **UI and development:** FastAPI OpenAPI `/docs`, a lightweight browser UI and Docker Compose.

Provider neutrality means **portability of the stored context and interfaces**, not automatic feature parity across proprietary model APIs. No cloud provider is required for basic operation.

## 9. Security, scale and limitations

The public edition is for **one person on localhost**. It lacks a multi-user authentication layer, role-specific permissions, production rate limiting and high-scale vector indexes. The optional API key is a shared secret, not tenant isolation. SQLite and in-memory retrieval fit small deployments; do not advertise enterprise-scale performance without measurement.

Imported sources may contain secrets, personal data or malicious instructions. Do not treat retrieved text as trusted instructions. Review sensitive material before import and secure backups.

**No measured token reduction, latency or answer-accuracy percentages are claimed here.** Such claims require a fixed corpus, reproducible benchmark, tokenizer accounting and comparison against a defined baseline.

## 10. Next engineering steps

To turn the compact-first concept into an enforceable architecture, implement (1) token-aware context budgets, (2) typed summaries with provenance references, (3) automatic A → B → C expansion with authorization and audit logging, (4) retrieval evaluation and deduplication, (5) asynchronous imports, (6) versioned skill approval, and (7) scalable vector indexing when the corpus justifies it.

The guiding rule remains: **an AI agent should receive the smallest sufficient, inspectable context — while the complete source remains accessible when needed.**

---

**Public implementation:** [README](../README.md) · [Architecture](ARCHITECTURE.md) · [Feature Matrix](FEATURE_MATRIX.md) · [Русская версия](TECHNICAL_DESIGN_RU.md)
