import json

from sumer.chamber import play_council
from sumer.storage import Store
from tests.support import unanimous


def test_trace_import_is_incremental_and_waits_for_complete_line(tmp_path):
    store = Store(":memory:")
    store.create_run("test-run", {})
    span = {"traceId": "a" * 32, "spanId": "b" * 16, "name": "agent.phase",
            "attributes": [{"key": "sumer.run_id", "value": {"stringValue": "test-run"}}]}
    record = json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]})
    path = tmp_path / "spans.jsonl"
    path.write_text(record)
    assert store.import_traces(path) == 0
    path.write_text(record + "\n")
    assert store.import_traces(path) == 1
    assert store.import_traces(path) == 0
    assert store.traces("test-run") == [span]


async def test_trace_collection_failure_preserves_game_result(monkeypatch):
    from sumer import chamber
    def unavailable():
        raise OSError("Collector unavailable")
    monkeypatch.setattr(chamber, "flush", unavailable)
    journal = Store(":memory:")
    g = await play_council(1, unanimous, store=journal, max_rounds=1)
    assert g.status in ("completed", "overthrown", "depopulated")
    assert any(e["kind"] == "observability_error" for e in journal.events(g.run_id))
    assert journal.run(g.run_id)["result"]["status"] == g.status
