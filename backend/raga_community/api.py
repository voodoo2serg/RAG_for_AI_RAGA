"""RAGA Community local-first knowledge and context API."""
from __future__ import annotations
import hmac
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from .extract import extract_knowledge, rank_text

DB_PATH = Path(os.getenv("RAGA_DB_PATH", "./data/raga.sqlite3"))
API_KEY = os.getenv("RAGA_API_KEY", "")
app = FastAPI(title="RAGA Community API", version="0.1.0")

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()

@app.on_event("startup")
def startup():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
          source TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(
          id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
          session_id TEXT REFERENCES sessions(id), type TEXT NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_items_project ON items(project_id,type);
        """)

def auth(x_api_key: str | None = Header(default=None)):
    if API_KEY and not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Invalid API key")

def require_project(c, pid):
    if not c.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
        raise HTTPException(404, "Project not found")

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class SessionIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000000)
    source: str = "manual"

class ItemIn(BaseModel):
    type: Literal["skill","rule","ticket","decision","note"]
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    status: Literal["draft","active","done","archived"] = "draft"

class ItemUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    status: Literal["draft","active","done","archived"] | None = None

@app.get("/api/health")
def health():
    return {"status":"ok","edition":"community"}

@app.get("/api/projects", dependencies=[Depends(auth)])
def projects():
    with db() as c:
        return [dict(x) for x in c.execute("SELECT * FROM projects ORDER BY created_at DESC")]

@app.post("/api/projects", dependencies=[Depends(auth)], status_code=201)
def create_project(payload: ProjectIn):
    item = {"id":str(uuid.uuid4()),"name":payload.name,"created_at":now()}
    with db() as c:
        c.execute("INSERT INTO projects VALUES (:id,:name,:created_at)", item)
    return item

@app.post("/api/projects/{pid}/sessions", dependencies=[Depends(auth)], status_code=201)
def import_session(pid: str, payload: SessionIn):
    sid = str(uuid.uuid4())
    from .knowledge import index_session, ensure_schema
    found = extract_knowledge(payload.content)
    with db() as c:
        ensure_schema(c)
        require_project(c,pid)
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                  (sid,pid,payload.source,payload.content,now()))
        index_session(c, pid, sid, payload.content)
        for kind,values in found.items():
            for val in values:
                c.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()),pid,sid,kind,val[:300],val,"draft",now()))
    return {"session_id":sid,"extracted":{k:len(v) for k,v in found.items()}}

@app.get("/api/projects/{pid}/sessions", dependencies=[Depends(auth)])
def sessions(pid: str):
    with db() as c:
        require_project(c,pid)
        return [dict(x) for x in c.execute("SELECT id,source,created_at FROM sessions WHERE project_id=? ORDER BY created_at DESC",(pid,))]

@app.get("/api/projects/{pid}/items", dependencies=[Depends(auth)])
def items(pid: str, type: str | None = None):
    with db() as c:
        require_project(c,pid)
        q="SELECT * FROM items WHERE project_id=?"
        args=[pid]
        if type:
            q+=" AND type=?"
            args.append(type)
        return [dict(x) for x in c.execute(q+" ORDER BY created_at DESC",args)]

@app.post("/api/projects/{pid}/items", dependencies=[Depends(auth)], status_code=201)
def create_item(pid: str, payload: ItemIn):
    item={"id":str(uuid.uuid4()),"project_id":pid,"session_id":None,
          "type":payload.type,"title":payload.title,"body":payload.body,
          "status":payload.status,"created_at":now()}
    with db() as c:
        require_project(c,pid)
        c.execute("""INSERT INTO items VALUES
                  (:id,:project_id,:session_id,:type,:title,:body,:status,:created_at)""",item)
    return item

@app.patch("/api/projects/{pid}/items/{item_id}", dependencies=[Depends(auth)])
def update_item(pid: str, item_id: str, payload: ItemUpdate):
    patch=payload.model_dump(exclude_none=True)
    with db() as c:
        require_project(c,pid)
        row=c.execute("SELECT * FROM items WHERE id=? AND project_id=?",(item_id,pid)).fetchone()
        if not row:
            raise HTTPException(404,"Item not found")
        updated={**dict(row),**patch}
        c.execute("UPDATE items SET title=:title,body=:body,status=:status WHERE id=:id",updated)
        return updated

@app.get("/api/projects/{pid}/search", dependencies=[Depends(auth)])
def search(pid: str, q: str, limit: int = 10):
    with db() as c:
        require_project(c,pid)
        rows=[dict(x) for x in c.execute("SELECT * FROM items WHERE project_id=?",(pid,))]
    scored=({**x,"score":rank_text(q,x["title"]+" "+x["body"])} for x in rows)
    return sorted(scored,key=lambda x:x["score"],reverse=True)[:max(1,min(limit,100))]

@app.get("/api/projects/{pid}/continuity", dependencies=[Depends(auth)])
def continuity(pid: str):
    with db() as c:
        require_project(c,pid)
        project=dict(c.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone())
        items=[dict(x) for x in c.execute(
            "SELECT * FROM items WHERE project_id=? AND status NOT IN ('done','archived') ORDER BY created_at DESC",(pid,))]
    groups={k:[x for x in items if x["type"]==k] for k in ("rule","decision","ticket","skill","note")}
    lines=["# Context recovery: "+project["name"],""]
    for kind,vals in groups.items():
        lines+=["## "+kind.title()+"s"]+["- "+x["title"] for x in vals[:50]]+[""]
    return {"project":project,"groups":groups,"markdown":"\n".join(lines)}


from fastapi.responses import HTMLResponse, FileResponse

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def workspace():
    return (Path(__file__).parent / "webui.html").read_text(encoding="utf-8")

@app.get("/static/logo.png", include_in_schema=False)
def logo():
    return FileResponse(Path(__file__).parent / "logo.png", media_type="image/png")


@app.get("/api/projects/{pid}/sessions/{session_id}", dependencies=[Depends(auth)])
def read_session(pid: str, session_id: str):
    with db() as c:
        require_project(c, pid)
        row = c.execute("SELECT * FROM sessions WHERE id=? AND project_id=?", (session_id, pid)).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        return dict(row)

@app.get("/api/projects/{pid}/backup", dependencies=[Depends(auth)])
def backup(pid: str):
    """Portable JSON backup of project and its entire session/knowledge content."""
    with db() as c:
        require_project(c, pid)
        project = dict(c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
        sessions = [dict(x) for x in c.execute("SELECT * FROM sessions WHERE project_id=?", (pid,))]
        items = [dict(x) for x in c.execute("SELECT * FROM items WHERE project_id=?", (pid,))]
        return {"format": "raga-community-backup-v1", "project": project,
                "sessions": sessions, "items": items}

@app.get("/api/projects/{pid}/skills/{item_id}/markdown", dependencies=[Depends(auth)])
def skill_markdown(pid: str, item_id: str):
    """Export a hand-authored skill as portable Markdown content."""
    with db() as c:
        require_project(c, pid)
        row = c.execute("SELECT * FROM items WHERE id=? AND project_id=? AND type='skill'",
                        (item_id, pid)).fetchone()
        if not row:
            raise HTTPException(404, "Skill not found")
        return {"filename": "SKILL.md",
                "markdown": "# " + row["title"] + "\n\n" + row["body"]}


from .knowledge import router as knowledge_router
app.include_router(knowledge_router)

from .semantic import router as semantic_router
app.include_router(semantic_router)


@app.delete("/api/projects/{pid}/items/{item_id}", dependencies=[Depends(auth)])
def delete_item(pid: str, item_id: str):
    with db() as c:
        require_project(c, pid)
        deleted = c.execute("DELETE FROM items WHERE project_id=? AND id=?", (pid, item_id)).rowcount
        if not deleted:
            raise HTTPException(404, "Item not found")
    return {"deleted": True}


@app.delete("/api/projects/{pid}/sessions/{sid}", dependencies=[Depends(auth)])
def delete_session(pid: str, sid: str):
    from .knowledge import ensure_schema
    from .semantic import ensure_vector_schema
    with db() as c:
        ensure_schema(c)
        ensure_vector_schema(c)
        require_project(c, pid)
        if not c.execute("SELECT 1 FROM sessions WHERE id=? AND project_id=?", (sid, pid)).fetchone():
            raise HTTPException(404, "Session not found")
        c.execute("""DELETE FROM chunk_vectors WHERE chunk_id IN
                     (SELECT id FROM chunks WHERE session_id=? AND project_id=?)""", (sid, pid))
        c.execute("DELETE FROM chunks WHERE session_id=? AND project_id=?", (sid, pid))
        c.execute("DELETE FROM items WHERE session_id=? AND project_id=?", (sid, pid))
        c.execute("DELETE FROM sessions WHERE id=? AND project_id=?", (sid, pid))
    return {"deleted": True}


@app.delete("/api/projects/{pid}", dependencies=[Depends(auth)])
def delete_project(pid: str):
    from .knowledge import ensure_schema
    from .semantic import ensure_vector_schema
    with db() as c:
        ensure_schema(c)
        ensure_vector_schema(c)
        require_project(c, pid)
        c.execute("""DELETE FROM chunk_vectors WHERE chunk_id IN
                     (SELECT id FROM chunks WHERE project_id=?)""", (pid,))
        c.execute("DELETE FROM chunks WHERE project_id=?", (pid,))
        c.execute("DELETE FROM items WHERE project_id=?", (pid,))
        c.execute("DELETE FROM sessions WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"deleted": True}
