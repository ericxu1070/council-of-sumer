import json
import time

import pytest
from fastapi.testclient import TestClient

from sumer.storage import Store
from tests.support import unanimous


@pytest.fixture
def chamber(monkeypatch):
    from sumer import web

    journal = Store(":memory:")
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    monkeypatch.setattr(web, "_ask", unanimous)
    async def skip_scout(year, round_no, phase, rows):
        await web.journal_emit("scout_choice", {"year": year, "round": round_no,
                                               "phase": phase, "choice": "skip"})
        return "skip"
    monkeypatch.setattr(web, "_ask_scout", skip_scout)
    web.live.pace_s = 0
    with TestClient(web.app) as client:
        yield client, journal, web
    journal.db.close()


@pytest.mark.parametrize("advisors", [
    [],
    [{"id": "priest", "bias": 1.1}],
    [{"id": "villager", "bias": "0.5"}],
    [{"id": "farmer"}, {"id": "farmer"}],
    [{"id": "unknown"}],
    [{"id": "custom-scribe", "short": "Scribe", "mandate": "Feed the city.",
      "color": "red;position:fixed"}],
])
def test_invalid_council_rejected_before_creating_run(chamber, advisors):
    client, journal, web = chamber
    response = client.post("/api/game", json={"advisors": advisors})
    assert response.status_code == 422
    assert journal.runs() == []
    assert web.live.run_id == ""


@pytest.mark.parametrize("advisors", [
    [{"id": "provider", "bias": 0.7}, {"id": "priest", "bias": 0}],
    [{"id": "custom-scribe", "short": "Scribe", "title": "Keeper of the Tablets",
      "mandate": "Speak for the families and keep everyone fed.",
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
    for seat, stored in zip(advisors, roster):
        if "bias" in seat:
            assert stored["bias"] == seat["bias"]
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert next(e["data"]["advisors"] for e in events if e["kind"] == "game") == roster
    turns = [e["data"] for e in events if e["kind"] == "turn"]
    assert turns and {t["advisor"] for t in turns} == set(ids)
    assert not any(e["kind"] == "technical_failure" for e in events)
    assert len(client.get("/api/meta").json()["advisors"]) == 5
    assert client.get("/api/meta").json()["max_advisors"] == 21
    exports = journal.exports(run_id)
    assert json.loads(exports["summary.json"])["manifest"]["advisors"] == roster
    client.post("/api/stop")
