from fastapi.testclient import TestClient
from raga_community import api

def test_project_import_retrieval_and_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.sqlite3")
    api.startup()
    with TestClient(api.app) as client:
        project = client.post("/api/projects", json={"name":"Research notes"})
        assert project.status_code == 201
        pid = project.json()["id"]
        source = client.post(f"/api/projects/{pid}/sessions", json={
            "source":"agent-chat",
            "content":"Decision: use local PostgreSQL for deployment. Rule: check citations.\n\nTODO: validate retrieval."})
        assert source.status_code == 201, source.text
        got = client.post(f"/api/projects/{pid}/retrieve", json={"query":"PostgreSQL deployment"})
        assert got.status_code == 200, got.text
        results = got.json()["sources"]
        assert results and results[0]["session_id"] == source.json()["session_id"]
        ask = client.post(f"/api/projects/{pid}/ask", json={"query":"PostgreSQL deployment"})
        assert ask.status_code == 200
        assert ask.json()["model_used"] is False
        backup = client.get(f"/api/projects/{pid}/backup")
        assert backup.status_code == 200
        assert backup.json()["sessions"][0]["content"].startswith("Decision:")
        restore_target = client.post("/api/projects", json={"name":"Restored"}).json()["id"]
        restored = client.post(f"/api/projects/{restore_target}/restore", json=backup.json())
        assert restored.status_code == 201, restored.text
        check = client.post(f"/api/projects/{restore_target}/retrieve", json={"query":"PostgreSQL deployment"})
        assert check.json()["sources"]
        assert client.get("/").status_code == 200
        assert client.get("/static/logo.png").status_code == 200

def test_text_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "upload.sqlite3")
    api.startup()
    with TestClient(api.app) as client:
        pid=client.post("/api/projects",json={"name":"Files"}).json()["id"]
        uploaded=client.post(f"/api/projects/{pid}/upload",
               files={"file":("notes.md",b"Rule: verify archive sources before publication","text/markdown")})
        assert uploaded.status_code == 200,uploaded.text
        assert uploaded.json()["count"] == 1
        assert client.post(f"/api/projects/{pid}/retrieve",
            json={"query":"publication archive"}).json()["sources"]


def test_deletion_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "deletion.sqlite3")
    api.startup()
    with TestClient(api.app) as client:
        pid = client.post("/api/projects", json={"name":"Temporary project"}).json()["id"]
        sid = client.post(f"/api/projects/{pid}/sessions", json={
            "content":"Rule: retain project history while relevant"}).json()["session_id"]
        item = client.post(f"/api/projects/{pid}/items", json={
            "type":"skill","title":"Manual skill","body":"Inspect context"}).json()["id"]
        assert client.delete(f"/api/projects/{pid}/items/{item}").status_code == 200
        assert client.delete(f"/api/projects/{pid}/sessions/{sid}").status_code == 200
        assert not client.post(f"/api/projects/{pid}/retrieve", json={"query":"history"}).json()["sources"]
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        assert client.get(f"/api/projects/{pid}/items").status_code == 404
