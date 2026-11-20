"""Full-text retrieval, import adapters and optional local model Q&A.

All records belong to a project. This module has no private extension imports.
The default mode works offline. Optional Ollama requests use the local model API.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sqlite3
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .api import auth, db, require_project, now
from .extract import extract_knowledge

router = APIRouter(prefix="/api/projects/{pid}", tags=["knowledge"], dependencies=[Depends(auth)])
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)
MAX_FILE = 5 * 1024 * 1024
MAX_ARCHIVE = 20 * 1024 * 1024
DEFAULT_CHUNK = 1100
MAX_CHUNKS = 2000


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold())


def chunk_text(text: str, size: int = DEFAULT_CHUNK, overlap: int = 150) -> list[str]:
    """Paragraph-aware segmentation with bounded overlap for unstructured text."""
    if not text:
        return []
    size = max(256, min(size, 4000))
    overlap = max(0, min(overlap, size // 3))
    paragraphs = re.split(r"\n\s*\n", text)
    pieces = []
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", size // 2, size + 1)
            if cut < size // 2:
                cut = size
            piece = paragraph[:cut].strip()
            if piece:
                pieces.append(piece)
            paragraph = paragraph[max(1, cut - overlap):].strip()
        if paragraph:
            pieces.append(paragraph)
    chunks, buffer = [], ""
    for piece in pieces:
        if buffer and len(buffer) + len(piece) + 2 > size:
            chunks.append(buffer)
            buffer = (buffer[-overlap:] + "\n" if overlap else "") + piece
        else:
            buffer = (buffer + "\n\n" + piece).strip() if buffer else piece
        if len(chunks) >= MAX_CHUNKS:
            break
    if buffer and len(chunks) < MAX_CHUNKS:
        chunks.append(buffer)
    return chunks


def ensure_schema(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chunks(
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(session_id,chunk_index)
    );
    CREATE INDEX IF NOT EXISTS chunks_by_project ON chunks(project_id,session_id);
    """)


def index_session(c: sqlite3.Connection, pid: str, sid: str, content: str) -> int:
    chunks = chunk_text(content)
    c.executemany(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
        [(str(uuid.uuid4()), pid, sid, i, val, now()) for i, val in enumerate(chunks)]
    )
    return len(chunks)


def retrieve(c: sqlite3.Connection, pid: str, query: str, limit: int = 8) -> list[dict]:
    """BM25-like retrieval across the source chunks with stable source IDs."""
    q = Counter(tokens(query))
    if not q:
        return []
    rows = [dict(x) for x in c.execute(
        """SELECT c.*,s.source FROM chunks c JOIN sessions s ON s.id=c.session_id
           WHERE c.project_id=?""", (pid,))]
    if not rows:
        return []
    docs = [Counter(tokens(x["content"])) for x in rows]
    N = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / N
    dfs = Counter(t for d in docs for t in d)
    found = []
    for row, counts in zip(rows, docs):
        score = 0.0
        dl = sum(counts.values())
        for term, qfreq in q.items():
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (N - dfs[term] + 0.5) / (dfs[term] + 0.5))
            score += idf * (tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / max(1, avgdl)))) * min(qfreq, 3)
        if score > 0:
            found.append({
                "chunk_id": row["id"],
                "session_id": row["session_id"],
                "source": row["source"],
                "chunk_index": row["chunk_index"],
                "score": round(score, 4),
                "text": row["content"],
            })
    return sorted(found, key=lambda x: x["score"], reverse=True)[:max(1, min(limit, 30))]


def parse_text_file(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in (".txt", ".md", ".json", ".jsonl", ".csv", ".log"):
        raise ValueError("Unsupported file type: " + suffix)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Upload UTF-8 text files") from exc
    if suffix == ".json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed JSON") from exc
        entries = []
        def walk(value):
            if isinstance(value, dict):
                for key in ("content", "text", "message"):
                    if isinstance(value.get(key), str):
                        entries.append(value[key])
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(obj)
        return "\n".join(entries) if entries else text
    return text


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=8, ge=1, le=30)


class AskIn(SearchIn):
    model: str = Field(default="llama3.2", max_length=100)
    use_model: bool = False


class RestoreIn(BaseModel):
    format: str
    project: dict
    sessions: list[dict] = Field(max_length=250)
    items: list[dict] = Field(max_length=5000)


def import_one(c: sqlite3.Connection, pid: str, source: str, content: str) -> dict:
    if not content.strip():
        raise ValueError("Empty document")
    if len(content) > 2_000_000:
        raise ValueError("Document exceeds 2 million characters")
    sid = str(uuid.uuid4())
    c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, pid, source[:200], content, now()))
    amount = index_session(c, pid, sid, content)
    extracted = extract_knowledge(content)
    for kind, values in extracted.items():
        for line in values:
            c.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), pid, sid, kind, line[:300], line, "draft", now()))
    return {"session_id": sid, "source": source, "chunks": amount,
            "candidates": sum(len(v) for v in extracted.values())}


@router.post("/upload")
async def upload(pid: str, file: UploadFile = File(...)):
    name = Path(file.filename or "notes.txt").name
    data = await file.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise HTTPException(413, "File is too large")
    imports = []
    try:
        with db() as c:
            ensure_schema(c)
            require_project(c, pid)
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    files = [x for x in archive.infolist() if not x.is_dir()]
                    if len(files) > 30 or sum(x.file_size for x in files) > MAX_ARCHIVE:
                        raise ValueError("Archive limits exceeded")
                    for item in files:
                        if Path(item.filename).suffix.lower() not in (".md",".txt",".json",".jsonl",".csv",".log"):
                            continue
                        if item.file_size > MAX_FILE:
                            raise ValueError("Archive entry too large")
                        content = parse_text_file(item.filename, archive.read(item))
                        imports.append(import_one(c, pid, Path(item.filename).name, content))
            else:
                if len(data) > MAX_FILE:
                    raise ValueError("Single upload limit is 5 MB")
                imports.append(import_one(c, pid, name, parse_text_file(name, data)))
    except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"imported": imports, "count": len(imports)}


@router.post("/retrieve")
def search_sources(pid: str, payload: SearchIn):
    with db() as c:
        ensure_schema(c)
        require_project(c, pid)
        return {"query": payload.query, "sources": retrieve(c, pid, payload.query, payload.limit)}


@router.post("/reindex")
def reindex(pid: str):
    with db() as c:
        ensure_schema(c)
        require_project(c, pid)
        rows = c.execute("SELECT id,content FROM sessions WHERE project_id=?", (pid,)).fetchall()
        count = 0
        for row in rows:
            c.execute("DELETE FROM chunks WHERE session_id=?", (row["id"],))
            count += index_session(c, pid, row["id"], row["content"])
        return {"sessions": len(rows), "chunks": count}


def ollama_answer(model: str, query: str, sources: list[dict]) -> str:
    endpoint = os.getenv("RAGA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:", "http://ollama:")):
        raise ValueError("Configure a trusted local Ollama endpoint")
    context = "\n\n".join(
        "[" + str(i + 1) + "] " + x["text"][:1400] for i, x in enumerate(sources)
    )
    prompt = (
        "Answer the question ONLY from the cited source passages. "
        "Cite the passage numbers in square brackets. If evidence is insufficient, say so.\n\n"
        + context + "\n\nQuestion: " + query
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = request.Request(endpoint + "/api/generate", data=body,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=60) as response:
            return json.load(response).get("response", "")
    except (error.URLError, TimeoutError) as exc:
        raise HTTPException(503, "Local model unavailable: " + str(exc)) from exc


@router.post("/ask")
def ask(pid: str, payload: AskIn):
    with db() as c:
        ensure_schema(c)
        require_project(c, pid)
        sources = retrieve(c, pid, payload.query, payload.limit)
    if not payload.use_model:
        return {"answer": None, "model_used": False, "sources": sources,
                "message": "Source retrieval works without a model. Set use_model=true for optional local Ollama answer."}
    if not sources:
        return {"answer": "Not enough information in the project.", "model_used": False, "sources": []}
    answer = ollama_answer(payload.model, payload.query, sources)
    return {"answer": answer, "model_used": True, "sources": sources}


@router.post("/restore", status_code=201)
def restore(pid: str, payload: RestoreIn):
    """Restore into an existing empty project; never trust exported IDs."""
    if payload.format != "raga-community-backup-v1":
        raise HTTPException(400, "Unsupported backup format")
    try:
        with db() as c:
            ensure_schema(c)
            require_project(c, pid)
            if c.execute("SELECT 1 FROM sessions WHERE project_id=? LIMIT 1", (pid,)).fetchone():
                raise ValueError("Restore requires an empty project")
            if c.execute("SELECT 1 FROM items WHERE project_id=? LIMIT 1", (pid,)).fetchone():
                raise ValueError("Restore requires an empty project")
            remap = {}
            for s in payload.sessions:
                content = str(s.get("content", ""))
                if len(content) > 2_000_000:
                    raise ValueError("Session too large")
                new_id = str(uuid.uuid4())
                remap[str(s.get("id", ""))] = new_id
                c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                          (new_id, pid, str(s.get("source","restored"))[:200], content, now()))
                index_session(c, pid, new_id, content)
            for item in payload.items:
                kind = str(item.get("type",""))
                if kind not in ("skill","rule","ticket","decision","note"):
                    continue
                status = str(item.get("status","draft"))
                if status not in ("draft","active","done","archived"):
                    status = "draft"
                sid = remap.get(str(item.get("session_id", "")))
                c.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()), pid, sid, kind,
                           str(item.get("title", ""))[:300],
                           str(item.get("body", ""))[:200000],
                           status, now()))
        return {"restored_sessions": len(payload.sessions), "restored_items": len(payload.items)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
