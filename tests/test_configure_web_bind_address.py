from dataclasses import replace

import pytest
import uvicorn

from sumer import cli


@pytest.mark.parametrize("args, host, port", [(["--host", "127.0.0.1", "--port", "8080"], "127.0.0.1", 8080), ([], "192.0.2.10", 9000)])
def test_web_cli_uses_requested_bind_address(monkeypatch, args, host, port):
    monkeypatch.setattr(cli, "settings", replace(cli.settings, web_host="192.0.2.10", web_port=9000))
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr("sys.argv", ["sumer", "web", *args])
    cli.main()
    assert calls[0]["host"] == host and calls[0]["port"] == port


@pytest.mark.parametrize("port", ["0", "65536", "oops"])
def test_invalid_port_is_rejected_before_launch(monkeypatch, port):
    monkeypatch.setattr("sys.argv", ["sumer", "web", "--port", port])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("host, expected", [("0.0.0.0", "localhost"), ("::", "localhost"), ("::1", "[::1]"), ("192.0.2.10", "192.0.2.10")])
def test_launcher_browser_host(host, expected):
    assert cli._browser_host(host) == expected
