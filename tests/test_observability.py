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


async def test_trace_context_is_covered_by_the_signature():
    import pytest

    from sumer.agent import decide
    from sumer.game import State
    from sumer.protocol import Brief, validate_reply, verify
    brief = Brief(seed=1, year=1, round=0, max_rounds=3, state=State().public(),
                  beliefs={}, speaker="farmer", traceparent="test-context")
    reply = await decide("farmer", brief)
    assert verify(reply)
    assert not verify(reply.model_copy(update={"traceparent": "altered-context"}))
    with pytest.raises(ValueError, match="request"):
        validate_reply(reply, brief.model_copy(update={"traceparent": "altered-request"}))


def test_oasf_records_cover_all_seven_current_advisors():
    from sumer.advisors import ADVISORS
    from sumer.config import ROOT
    for advisor in ADVISORS.values():
        record = json.loads((ROOT / "oasf" / f"{advisor.id}.json").read_text())
        assert advisor.title in record["name"]
        assert advisor.mandate in record["description"]
        assert "seven-advisor" in record["description"]
        assert "Three of four" not in record["description"]
