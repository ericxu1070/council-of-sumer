import pytest
from fastapi.testclient import TestClient

from sumer import web


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_invalid_pace_does_not_change_playback(monkeypatch, value):
    monkeypatch.setattr(web, "live", web.Live())
    web.live.pace_s = 1.25
    client = TestClient(web.app)
    assert client.post("/api/pace", json={"pace_s": value}).status_code == 422
    assert web.live.pace_s == 1.25


@pytest.mark.parametrize("value, expected", [(-1, 0), (2.5, 2.5), (20, 10)])
def test_finite_pace_preserves_clamping(value, expected):
    client = TestClient(web.app)
    assert client.post("/api/pace", json={"pace_s": value}).json()["pace_s"] == expected
