import asyncio

from starlette.requests import Request

from sumer import web
from sumer.storage import Store


async def test_slow_subscriber_detaches_without_blocking_others():
    hub = web.Hub()
    slow, fast = asyncio.Queue(maxsize=1), asyncio.Queue(maxsize=4)
    hub.subscribers.update({("r", slow), ("r", fast)})
    await hub.emit("turn", {"_run_id":"r", "_sequence":1})
    await hub.emit("turn", {"_run_id":"r", "_sequence":2})
    assert ("r", slow) not in hub.subscribers
    assert slow.get_nowait() is None
    assert [fast.get_nowait()["sequence"], fast.get_nowait()["sequence"]] == [1, 2]


async def test_stream_overflow_closes_and_replays_missing_events(monkeypatch):
    journal = Store(":memory:")
    journal.create_run("r", {})
    first = journal.append("r", "turn", {})
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "hub", web.Hub())
    async def receive():
        return {"type":"http.request"}
    response = await web.events(Request({"type":"http", "headers":[]}, receive), "r")
    await anext(response.body_iterator)
    queue = next(iter(web.hub.subscribers))[1]
    assert 0 < queue.maxsize <= 256
    for _ in range(queue.maxsize + 1):
        event = journal.append("r", "turn", {})
        await web.hub.emit("turn", {"_run_id":"r", "_sequence":event["sequence"]})
    try:
        await anext(response.body_iterator)
        assert False, "overflow should end the stream"
    except StopAsyncIteration:
        pass
    assert not web.hub.subscribers
    request = Request({"type":"http", "headers":[(b"last-event-id", f"r:{first['sequence']}".encode())]}, receive)
    replay = await web.events(request, "r")
    assert f"id: r:{first['sequence'] + 1}" in await anext(replay.body_iterator)
    await replay.body_iterator.aclose()
    assert not web.hub.subscribers
    journal.db.close()
