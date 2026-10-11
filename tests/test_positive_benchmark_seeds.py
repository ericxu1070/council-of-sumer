import pytest
from sumer import bench, cli


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
async def test_bad_seed_count_preserves_results(monkeypatch, tmp_path, count):
    monkeypatch.setattr(bench, "RESULTS_DIR", tmp_path)
    existing = tmp_path / "bench.json"
    existing.write_text("preserve me")
    with pytest.raises(ValueError, match="positive integer"):
        await bench.run(count)
    assert existing.read_text() == "preserve me"


def test_cli_reports_bad_seed_count(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sumer", "bench", "--seeds", "0"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "positive integer" in capsys.readouterr().err
