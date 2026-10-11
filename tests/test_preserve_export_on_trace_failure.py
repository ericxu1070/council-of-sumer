import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from sumer import web
from sumer.storage import Store


@pytest.mark.parametrize("failure", [OSError("collector unavailable"), ValueError("bad trace data")])
def test_trace_import_failure_preserves_archive(monkeypatch, failure, caplog):
    journal = Store(":memory:")
    journal.create_run("r", {"seed": 1})
    journal.status("r", "completed")
    journal.append("r", "turn", {"year": 1})
    def fail(*args):
        raise failure
    monkeypatch.setattr(journal, "import_traces", fail)
    monkeypatch.setattr(web, "store", journal)
    client = TestClient(web.app)
    response = client.get("/api/runs/r/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert '"seed": 1' in archive.read("summary.json").decode()
        assert '"kind": "turn"' in archive.read("events.jsonl").decode()
    assert client.get("/api/runs/r/traces").json() == []
    assert "Trace import failed" in caplog.text
    assert client.get("/api/runs/missing/export").status_code == 404
    journal.db.close()
