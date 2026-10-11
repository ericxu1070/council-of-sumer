from fastapi.testclient import TestClient
from sumer.storage import Store
from sumer import web


def test_startup_recovers_old_active_runs(monkeypatch):
    journal = Store(":memory:")
    for name in ("old-running", "old-deadlock"):
        journal.create_run(name, {})
    journal.status("old-deadlock", "deadlock")
    for i in range(105):
        journal.create_run(str(i), {})
        journal.status(str(i), "completed")
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    with TestClient(web.app):
        for name in ("old-running", "old-deadlock"):
            assert journal.run(name)["status"] == "technical_failure"
            assert journal.events(name)[-1]["kind"] == "technical_failure"
        assert journal.run("104")["status"] == "completed"
    journal.db.close()
