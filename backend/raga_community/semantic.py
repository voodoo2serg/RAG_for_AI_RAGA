"""Optional local vector retrieval using standard cosine similarity.

Independent generic implementation. Requires a local Ollama embedding model.
The default lexical retrieval continues to work without this optional module.
"""
from __future__ import annotations

import json
import math
import os
from urllib import error, request

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .api import auth, db, require_project
from .knowledge import ensure_schema, retrieve

router = APIRouter(prefix="/api/projects/{pid}", tags=["semantic search"], dependencies=[Depends(auth)])


def ensure_vector_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chunk_vectors(
      chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
      model TEXT NOT NULL,
      embedding TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS vector_model_idx ON chunk_vectors(model);
    """)


def local_embed(text: str, model: str = "nomic-embed-text") -> list[float]:
    endpoint = os.getenv("RAGA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:", "http://ollama:")):
        raise ValueError("Only a trusted local Ollama endpoint is supported")
    payload = json.dumps({"model": model, "input": text[:8000]}).encode()
    req = request.Request(endpoint + "/api/embed", data=payload,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=90) as response:
            raw = json.load(response)
    except (error.URLError, TimeoutError) as exc:
        raise HTTPException(503, "Local embedding service unavailable: " + str(exc)) from exc
    values = raw.get("embeddings")
    if not isinstance(values, list) or not values or not isinstance(values[0], list):
        raise HTTPException(502, "Invalid embedding response from local model")
    return [float(x) for x in values[0]]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    norm = math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b))
    return max(-1.0, min(1.0, dot / norm)) if norm else 0.0


class VectorizeIn(BaseModel):
    model: str = Field(default="nomic-embed-text", min_length=1, max_length=100)
    limit: int = Field(default=200, ge=1, le=2000)


class SemanticIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    model: str = Field(default="nomic-embed-text", min_length=1, max_length=100)
    limit: int = Field(default=8, ge=1, le=30)
    hybrid: bool = True


@router.post("/vectorize")
def vectorize(pid: str, payload: VectorizeIn):
    """Compute and store embeddings for the first unindexed source chunks.

    This is a simple local workflow; for very large corpora use a job queue.
    """
    with db() as c:
        ensure_schema(c)
        ensure_vector_schema(c)
        require_project(c, pid)
        rows = c.execute("""
          SELECT ch.id, ch.content FROM chunks ch
          LEFT JOIN chunk_vectors v ON v.chunk_id=ch.id AND v.model=?
          WHERE ch.project_id=? AND v.chunk_id IS NULL
          ORDER BY ch.created_at,ch.chunk_index LIMIT ?
        """, (payload.model, pid, payload.limit)).fetchall()
    embedded = []
    for row in rows:
        vec = local_embed(row["content"], payload.model)
        embedded.append((row["id"], payload.model, json.dumps(vec)))
    with db() as c:
        ensure_schema(c)
        ensure_vector_schema(c)
        require_project(c, pid)
        c.executemany(
            "INSERT OR REPLACE INTO chunk_vectors (chunk_id,model,embedding) VALUES (?,?,?)",
            embedded
        )
    return {"indexed": len(embedded), "model": payload.model}


@router.post("/semantic-search")
def semantic_search(pid: str, payload: SemanticIn):
    query_vector = local_embed(payload.query, payload.model)
    with db() as c:
        ensure_schema(c)
        ensure_vector_schema(c)
        require_project(c, pid)
        rows = c.execute("""
          SELECT ch.id, ch.session_id, ch.chunk_index, ch.content, s.source, v.embedding
          FROM chunk_vectors v JOIN chunks ch ON ch.id=v.chunk_id
          JOIN sessions s ON s.id=ch.session_id
          WHERE ch.project_id=? AND v.model=?
        """, (pid, payload.model)).fetchall()
        lexical = retrieve(c, pid, payload.query, max(payload.limit * 2, 20)) if payload.hybrid else []
    candidates = {}
    for row in rows:
        similarity = cosine(query_vector, json.loads(row["embedding"]))
        candidates[row["id"]] = {
            "chunk_id": row["id"], "session_id": row["session_id"],
            "chunk_index": row["chunk_index"], "source": row["source"],
            "text": row["content"], "semantic_score": round(similarity, 4),
            "lexical_score": 0.0,
        }
    for item in lexical:
        if item["chunk_id"] not in candidates:
            candidates[item["chunk_id"]] = {
                **item, "semantic_score": 0.0,
                "lexical_score": item["score"],
            }
        else:
            candidates[item["chunk_id"]]["lexical_score"] = item["score"]
    if payload.hybrid:
        lexical_max = max((x.get("lexical_score", 0.0) for x in candidates.values()), default=0)
    else:
        lexical_max = 0
    for item in candidates.values():
        sem = max(0, item.get("semantic_score", 0))
        lexical_norm = item.get("lexical_score", 0) / lexical_max if lexical_max else 0
        item["score"] = round(0.7 * sem + 0.3 * lexical_norm if payload.hybrid else sem, 4)
    results = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)[:payload.limit]
    return {"model": payload.model, "hybrid": payload.hybrid, "indexed_chunks": len(rows),
            "sources": results}
