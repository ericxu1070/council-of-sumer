import json

from fastapi.testclient import TestClient

from sumer import llm
from sumer.chamber import play_council
from sumer.storage import Store
from tests.support import unanimous


async def test_saved_replay_and_exports_do_not_call_models(tmp_path, monkeypatch):
    path = tmp_path / "runs.sqlite3"
    journal = Store(path)
    g = await play_council(1, unanimous, store=journal, max_rounds=1)
    recorded = journal.events(g.run_id)
    journal.db.close()
    def unexpected(*args, **kwargs):
        raise AssertionError("Replay must not call a model.")
    monkeypatch.setattr(llm, "chat", unexpected)
    reopened = Store(path)
    assert reopened.events(g.run_id) == recorded
    exports = reopened.exports(g.run_id)
    assert set(exports) == {"events.jsonl", "decisions.csv", "summary.json", "traces.jsonl", "report.md"}
    assert "yes_voters" in exports["decisions.csv"] and len(g.years) == 5
    assert "random_inputs" in exports["events.jsonl"]
    assert json.loads(exports["summary.json"])["status"] == g.status
    reopened.db.close()


def test_api_replay_export_and_wrong_human_selection(monkeypatch):
    from sumer import web
    journal = Store(":memory:")
    journal.create_run("recorded", {"seed": 7})
    journal.append("recorded", "phase", {"year": 1, "phase": "vote"})
    journal.status("recorded", "completed")
    monkeypatch.setattr(web, "store", journal)
    with TestClient(web.app) as client:
        assert client.get("/api/runs/recorded/events?year=1").json()[0]["data"]["phase"] == "vote"
        assert client.get("/api/runs/recorded/export").headers["content-type"] == "application/zip"
        assert client.get("/api/runs/missing").status_code == 404
        assert client.post("/api/news", json={"run_id": "recorded", "kind": "harvest"}).status_code == 409
        assert client.post("/api/escalation", json={"run_id": "recorded", "choice": "safe"}).status_code == 409



def test_restart_marks_interrupted_runs_and_preserves_evidence(monkeypatch):
    from sumer import web
    journal = Store(":memory:")
    for run_id, status in (("running", "running"), ("deadlock", "deadlock"), ("finished", "completed")):
        journal.create_run(run_id, {"seed": 7})
        journal.append(run_id, "phase", {"year": 1, "phase": "vote"})
        journal.status(run_id, status)
    before = {run_id: journal.events(run_id) for run_id in ("running", "deadlock", "finished")}
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    with TestClient(web.app) as client:
        runs = {run["id"]: run for run in client.get("/api/runs").json()}
        for run_id in ("running", "deadlock"):
            assert runs[run_id]["status"] == "technical_failure"
            events = client.get(f"/api/runs/{run_id}/events").json()
            assert events[:-1] == before[run_id]
            assert events[-1]["kind"] == "technical_failure"
            assert "restarted" in events[-1]["data"]["message"]
        assert runs["finished"]["status"] == "completed"
        assert client.get("/api/runs/finished/events").json() == before["finished"]
