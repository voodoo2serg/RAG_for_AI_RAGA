# RAGA Community architecture

The public distribution is a self-contained, single-user project-memory tool. It has no dependency on the original private repository or proprietary research modules.

## Data flow

1. Clients POST conversation text or upload a UTF-8 document.
2. The original source is preserved in SQLite, linked to a project.
3. Text is divided into overlapping chunks for source-grounded retrieval.
4. A deterministic extractor creates reviewable candidate tickets, rules, skills and decisions.
5. A person reviews or edits the items through the web interface or API.
6. Lexical retrieval ranks matching source chunks. Optional local embeddings add semantic/hybrid search.
7. Optional local Ollama answers a query from a bounded source selection, returning the evidence alongside its answer.
8. A continuity export packages active project items as Markdown. A full JSON backup preserves source material and item records for transfer.

## Trust boundaries

The client/host protects the database and its chat content. This edition has an optional shared API key but no individual accounts, scoped users or tenant authorization. Docker binds the exposed port to localhost. Reverse proxies and remote access are the operator's responsibility.

Model-produced text and imported conversations are untrusted. The source search ranks evidence; it does not guarantee accuracy. Generated answers must be independently checked. Secrets pasted into an AI chat will also be stored in the database and included in full backups.

## Storage schema

- projects: project identity and creation date
- sessions: original imported conversation text, source and date
- items: skill, rule, decision, ticket and note records, with status and source session association
- chunks: overlapping searchable source passages
- chunk_vectors: optional JSON-encoded local model embeddings (small datasets only)

## Code boundaries

- backend/raga_community/api.py — projects, item CRUD, continuity, backup and restoration
- backend/raga_community/knowledge.py — file/ZIP imports, chunking, lexical retrieval and optional Ollama Q&A
- backend/raga_community/semantic.py — generic embedding and cosine similarity; no proprietary vector optimization
- backend/raga_community/extract.py — deterministic extractors
- backend/raga_community/webui.html — local interface
- backend/tests — standalone unit and integration tests

## Integration example

A client can create a project, POST a transcript, query retrieval and retrieve a portable continuity Markdown package. All documented routes are described by OpenAPI at /docs.

There is no hidden dependency on another host, identity server, billing provider or business application.

## Known limitations

- This is a compact personal edition rather than the complete original multi-service RAGA.
- Import is currently synchronous, without a background queue or large document ingestion workflow.
- Vector similarity uses an in-memory Python scan over SQLite-stored embeddings; it is suitable for modest corpora, not high-scale search.
- Documents are supported as UTF-8 text; PDF and DOCX parsing are not part of this release.
- Multi-user access control, API rate limits, approvals, webhooks, source-level permissions and a full administrative UI are not yet provided.
- No promise of zero-knowledge encryption or end-to-end encryption is made.

## Development quality gates

GitHub Actions runs Python tests and Docker smoke tests. Passing CI proves those scenarios only; it does not certify production security. Any source import that relies on unpublished modules must be rejected before merge.
