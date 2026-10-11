import json

from starlette.requests import Request

from sumer import web
from sumer.storage import Store


async def test_sse_headers_and_replay_frame(monkeypatch):
    journal = Store(":memory:")
    journal.create_run("r", {})
    event = journal.append("r", "turn", {"year":1})
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "hub", web.Hub())
    response = await web.events(Request({"type":"http", "headers":[]}), "r")
    try:
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        frame = await anext(response.body_iterator)
        assert f"id: r:{event['sequence']}" in frame
        assert json.dumps(event) in frame
    finally:
        await response.body_iterator.aclose()
        journal.db.close()
