import json

from sumer.storage import Store


def test_malformed_trace_shapes_do_not_block_valid_spans(tmp_path):
    journal = Store(":memory:")
    journal.create_run("r", {})
    valid = {"traceId":"a" * 32, "spanId":"b" * 16,
             "attributes":[{"key":"sumer.run_id", "value":{"stringValue":"r"}}]}
    bad_attributes = {**valid, "spanId":"c" * 16, "attributes":[None, {"key":[]}, {"key":"sumer.run_id", "value":None}]}
    bad_id = {**valid, "traceId": []}
    records = [None, [], 42, {"resourceSpans":None}, {"resourceSpans":[None, {"scopeSpans":42}]},
        {"resourceSpans":[{"scopeSpans":[{"spans":[None, bad_attributes, bad_id, valid]}]}]}]
    path = tmp_path / "traces.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert journal.import_traces(path) == 1
    assert journal.traces("r") == [valid]
    assert journal.import_traces(path) == 0
    journal.db.close()
