import hashlib

import pytest
from fastapi.testclient import TestClient

from sumer import clamor, voice


@pytest.fixture
def client():
    from sumer import web

    return TestClient(web.app)


@pytest.fixture
def tts_calls(monkeypatch):
    calls = []

    async def fake_tts(speaker, text, model, voice_settings=None):
        calls.append((speaker, text, model))
        return b"mp3"

    monkeypatch.setattr(voice, "_tts", fake_tts)
    return calls


def test_phrases_are_short_shouts():
    for stance, options in clamor.PHRASES.items():
        assert 1 <= len(options) <= 3, stance
        for text in options:
            assert 2 <= len(text.split()) <= 4, text
            assert text.endswith("!")
            assert "\u2014" not in text


def test_phrase_index_wraps_and_unknown_stance_fails():
    assert clamor.phrase("sell_land", 0) == "Sell the land!"
    assert clamor.phrase("sell_land", 2) == clamor.phrase("sell_land", 0)
    with pytest.raises(KeyError):
        clamor.phrase("riot", 0)


def test_phrase_table_endpoint(client):
    data = client.get("/api/clamor").json()
    assert data["phrases"]["feed"][0] == "Feed the people!"
    assert "narrator" not in data["voices"]
    assert {"provider", "farmer", "merchant", "priest", "villager"} <= set(data["voices"])
    assert isinstance(data["voice"], bool)


def test_shout_audio_uses_only_whitelisted_text(client, tts_calls):
    r = client.get("/api/clamor/merchant/buy_land/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert "max-age" in r.headers["cache-control"]
    assert tts_calls == [("merchant", "More fields!", voice.ADVISOR_MODEL)]


@pytest.mark.parametrize("path", [
    "/api/clamor/narrator/feed/0",      # the narrator never clamors
    "/api/clamor/custom-scribe/feed/0",  # voices are keys, not advisor ids
    "/api/clamor/farmer/riot/0",         # unknown stance
    "/api/clamor/farmer/feed/9",         # out of range phrase
    "/api/clamor/farmer/feed/-1",
])
def test_shout_rejects_anything_outside_the_whitelist(client, tts_calls, path):
    assert client.get(path).status_code == 404
    assert tts_calls == []


def test_shout_without_voice_is_404(client, monkeypatch):
    async def no_voice(*args, **kwargs):
        return None

    monkeypatch.setattr(voice, "_tts", no_voice)
    assert client.get("/api/clamor/priest/store/0").status_code == 404


async def test_shout_is_served_from_disk_cache_without_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "enabled", lambda: False)
    text = clamor.phrase("store", 0)
    key = hashlib.sha256(f"{voice.voice_id('priest')}:{voice.ADVISOR_MODEL}:{text}".encode()).hexdigest()
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / f"{key}.mp3").write_bytes(b"cached")
    assert await clamor.shout("priest", "store", 0) == b"cached"
    assert await clamor.shout("priest", "store", 1) is None
