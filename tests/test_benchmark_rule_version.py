import json
from fastapi.testclient import TestClient
import pytest
from sumer import web
from sumer.game import RULE_VERSION


@pytest.mark.parametrize("version", [None, "old-hamurabi-rules"])
def test_outdated_benchmark_is_historical(monkeypatch, tmp_path, version):
    monkeypatch.setattr(web, "RESULTS_DIR", tmp_path)
    (tmp_path / "bench.json").write_text(json.dumps({"rule_version": version, "rows": []}))
    assert TestClient(web.app).get("/api/bench").json()["historical"] is True


def test_current_benchmark_remains_available(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "RESULTS_DIR", tmp_path)
    report = {"rule_version": RULE_VERSION, "rows": []}
    (tmp_path / "bench.json").write_text(json.dumps(report))
    assert TestClient(web.app).get("/api/bench").json() == report
