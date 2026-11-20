from fastapi.testclient import TestClient

from raga_community import api
from raga_community import semantic

def test_semantic_search_works_without_private_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "semantic.sqlite3")
    api.startup()
    monkeypatch.setattr(
        semantic, "local_embed",
        lambda text, model="nomic-embed-text": (
            [1.0, 0.0] if "publication" in text.lower() else [0.0, 1.0]
        )
    )
    with TestClient(api.app) as client:
        pid = client.post("/api/projects", json={"name": "Archive"}).json()["id"]
        source = client.post(f"/api/projects/{pid}/sessions", json={
            "content": "Decision: publication process needs citations and independent checking."
        })
        assert source.status_code == 201, source.text
        indexed = client.post(f"/api/projects/{pid}/vectorize", json={"model":"local-test"})
        assert indexed.status_code == 200, indexed.text
        assert indexed.json()["indexed"] > 0
        found = client.post(f"/api/projects/{pid}/semantic-search",
                            json={"query":"publication","model":"local-test"})
        assert found.status_code == 200, found.text
        assert found.json()["sources"]
        assert found.json()["sources"][0]["semantic_score"] > 0.9


def test_cosine_math():
    assert semantic.cosine([1, 0], [1, 0]) == 1.0
    assert semantic.cosine([1, 0], [0, 1]) == 0.0
    assert semantic.cosine([1, 0], [1]) == 0.0
