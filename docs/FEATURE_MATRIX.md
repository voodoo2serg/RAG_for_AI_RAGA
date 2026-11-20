# Functional status — RAGA Community

Status refers to the public edition, not to the private product.

| Capability | Status | Verification |
| --- | --- | --- |
| Create and list projects | Implemented | API integration test + Docker smoke |
| Import original conversation text | Implemented | API integration test |
| Upload Markdown, TXT, JSON, JSONL, CSV, LOG | Implemented | API file-upload test |
| Import supported files from ZIP | Implemented | Code path; additional ZIP-specific tests recommended |
| Conversation chunking and provenance | Implemented | API source retrieval test |
| Lexical/BM25-style source search | Implemented | API integration test |
| Manual skills, rules, tickets, decisions | Implemented | API CRUD and local interface |
| Keyword-based extraction | Implemented | Unit test |
| Context continuity export | Implemented | API integration test |
| Full JSON export and project restore | Implemented | API integration test |
| Optional local Ollama answering | Implemented, requires model | Not covered by automated live-model tests |
| Optional local embedding and semantic/hybrid retrieval | Implemented, requires model | API test with deterministic model mock |
| Local Docker deployment | Implemented | Docker smoke workflow |
| Multi-user authorization | Not implemented | Single-user/localhost only |
| Asynchronous high-volume ingestion | Not implemented | Out of scope |
| PDF and DOCX ingest | Not implemented | Future adapter |
| Provider-specific agent connectors | Not implemented | API usable by client integrations |
| High-scale vector index | Not implemented | SQLite small-project implementation |
| Full original private UI/feature parity | Not achieved | Intentionally separate edition |

The documentation uses “implemented” for a code path and links checks separately. Do not infer that untested external model or production deployments have been verified.
