import json
from dataclasses import replace

import httpx

from sumer import voice


async def test_voice_settings_and_format_separate_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "settings", replace(voice.settings, elevenlabs_api_key="test"))
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=f"audio-{len(requests)}".encode())
    client = httpx.AsyncClient
    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    first = await voice._tts("narrator", "A year passed.", "model", {"style": 0.2, "stability": 0.5})
    assert await voice._tts("narrator", "A year passed.", "model", {"stability": 0.5, "style": 0.2}) == first
    changed = await voice._tts("narrator", "A year passed.", "model", {"style": 0.8})
    assert changed != first
    monkeypatch.setattr(voice, "OUTPUT_FORMAT", "mp3_44100_64")
    assert await voice._tts("narrator", "A year passed.", "model", {"style": 0.8}) != changed
    assert len(requests) == 3
