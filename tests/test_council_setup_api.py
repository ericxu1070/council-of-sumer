import json
import time

import pytest
from fastapi.testclient import TestClient

from sumer.storage import Store


@pytest.fixture
def chamber(monkeypatch):
    from sumer import web

    journal = Store(":memory:")
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    web.live.pace_s = 0
    with TestClient(web.app) as client:
        yield client, journal, web
    journal.db.close()


@pytest.mark.parametrize("advisors", [
    [],
    [{"id": "farmer"}, {"id": "farmer"}],
    [{"id": "unknown"}],
    [{"id": "custom-scribe", "short": "Scribe", "profile": "farmer"}],
    [{"id": "custom-scribe", "short": "Scribe", "mandate": "Feed the city.",
      "profile": "unknown"}],
    [{"id": "bad/id", "short": "Scribe", "mandate": "Feed the city.",
      "profile": "farmer"}],
    [{"id": "custom-scribe", "short": "Scribe", "mandate": "Feed the city.",
      "profile": "farmer", "color": "red;position:fixed"}],
    [{"id": f"custom-{i}", "short": f"Voter {i}", "mandate": "Feed the city.",
      "profile": "farmer"} for i in range(22)],
])
def test_invalid_council_rejected_before_creating_run(chamber, advisors):
    client, journal, web = chamber
    response = client.post("/api/game", json={"advisors": advisors})
    assert response.status_code == 422
    assert journal.runs() == []
    assert web.live.run_id == ""


@pytest.mark.parametrize("advisors", [
    [{"id": "farmer"}],
    [{"id": "farmer"}, {"id": "builder"}],
    [{"id": "custom-scribe", "short": "Scribe", "title": "Keeper of the Tablets",
      "mandate": "Speak for the families and keep everyone fed.", "profile": "farmer",
      "voice": "patient, explains choices simply"}],
])
def test_selected_council_runs_and_replays_from_saved_roster(chamber, advisors):
    client, journal, _web = chamber
    response = client.post("/api/game", json={"seed": 1, "max_rounds": 2,
                                             "advisors": advisors})
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    # Reading the route also gives the server a chance to finish its unpaced run.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = client.get(f"/api/runs/{run_id}").json()
        if saved["status"] != "running":
            break
        time.sleep(0.01)
    assert saved["status"] in {"completed", "overthrown", "deadlock", "depopulated"}, saved
    roster = saved["manifest"]["advisors"]
    ids = [a["id"] for a in advisors]
    assert [a["id"] for a in roster] == ids
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert next(e["data"]["advisors"] for e in events if e["kind"] == "game") == roster
    turns = [e["data"] for e in events if e["kind"] == "turn"]
    assert turns and {t["advisor"] for t in turns} == set(ids)
    assert not any(e["kind"] == "technical_failure" for e in events)
    assert len(client.get("/api/meta").json()["advisors"]) == 7
    assert client.get("/api/meta").json()["max_advisors"] == 21
    exports = journal.exports(run_id)
    assert json.loads(exports["summary.json"])["manifest"]["advisors"] == roster
    client.post("/api/stop")
