import pytest
from fastapi.testclient import TestClient

from sumer import web
from sumer.storage import Store


def test_archive_pages_reach_older_runs(monkeypatch):
    journal = Store(":memory:")
    for i in range(105):
        journal.create_run(f"run-{i:03}", {})
    monkeypatch.setattr(web, "store", journal)
    client = TestClient(web.app)
    first = client.get("/api/runs?limit=100").json()
    second = client.get("/api/runs?limit=100&offset=100").json()
    assert len(first) == 100 and len(second) == 5
    assert len({r["id"] for r in first + second}) == 105
    assert client.get("/api/runs?offset=105").json() == []
    journal.db.close()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_archive_rejects_invalid_page(query):
    assert TestClient(web.app).get("/api/runs?" + query).status_code == 422
