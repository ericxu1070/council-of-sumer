from fastapi.testclient import TestClient

from sumer import web


def test_old_narration_url_cannot_play_new_game(monkeypatch):
    live = web.Live()
    live.game_no = 2
    live.narration[1] = b"current-game"
    monkeypatch.setattr(web, "live", live)
    with TestClient(web.app, raise_server_exceptions=True) as client:
        assert client.get("/api/narration/1?game=1").status_code == 404
        assert client.get("/api/narration/1?game=2").content == b"current-game"
        assert client.get("/api/narration/1").content == b"current-game"
        assert client.get("/api/narration/2?game=2").status_code == 404
