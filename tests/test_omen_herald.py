import asyncio

import pytest
from fastapi.testclient import TestClient

from sumer import voice
from sumer.scouts import SCOUT_IDS
from sumer.storage import Store


@pytest.fixture
def tts_calls(monkeypatch):
    calls = []

    async def fake_tts(speaker, text, model, voice_settings=None):
        calls.append((speaker, text))
        return b"x" * voice.BYTES_PER_SECOND

    monkeypatch.setattr(voice, "_tts", fake_tts)
    return calls


def test_every_omen_has_a_herald_line_in_the_right_mood():
    assert set(voice.OMEN_LINES) == SCOUT_IDS - {"skip"}
    moods = {omen: herald for omen, (herald, _) in voice.OMEN_LINES.items()}
    assert {o for o, h in moods.items() if h == "herald_dread"} == {"plague", "rats", "bad_harvest"}
    assert {o for o, h in moods.items() if h == "herald_joy"} == {"good_harvest", "low_price", "high_price"}
    for herald, text in voice.OMEN_LINES.values():
        assert voice.voice_id(herald) and "—" not in text


async def test_announce_uses_only_fixed_lines(tts_calls):
    assert await voice.announce("skip") is None
    assert await voice.announce("riot") is None
    assert await voice.announce("plague") is not None
    assert tts_calls == [("herald_dread", voice.OMEN_LINES["plague"][1])]


def test_omen_endpoint_serves_known_omens_only(tts_calls):
    from sumer import web

    client = TestClient(web.app)
    assert client.get("/api/omen/rats").headers["content-type"] == "audio/mpeg"
    assert client.get("/api/omen/skip").status_code == 404
    assert client.get("/api/omen/narrator").status_code == 404


async def test_scout_choice_carries_the_announcement(monkeypatch, tts_calls):
    from sumer import web

    journal = Store(":memory:")
    journal.create_run("herald", {})
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    web.live.run_id = "herald"
    web.live.pace_s = 0
    pending = asyncio.create_task(web._ask_scout(1, 0, "discuss", []))
    try:
        await asyncio.sleep(0)
        request = web.live.scout_request
        await web.scout(web.ScoutReq(run_id="herald", request_id=request["request_id"], choice="good_harvest"))
        assert await asyncio.wait_for(pending, 1) == "good_harvest"
        choice = next(e["data"] for e in journal.events("herald") if e["kind"] == "scout_choice")
        assert choice["announcement"] == {"text": voice.OMEN_LINES["good_harvest"][1], "mood": "joy",
                                          "url": "/api/omen/good_harvest"}
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        journal.db.close()
