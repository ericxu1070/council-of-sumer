"""A failed advisor must be visible to the hosting process supervisor."""

import logging

import pytest

from sumer import cli


def test_up_reports_child_failure(monkeypatch, caplog):
    class Process:
        def __init__(self, args, **kwargs):
            self.args = args
            self.returncode = -9 if args[-1] == "provider" else None

        def poll(self):
            return self.returncode

        def terminate(self):
            if self.returncode is None:
                self.returncode = 0

        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setenv("SUMER_OBSERVE", "false")
    monkeypatch.setattr(cli.subprocess, "Popen", Process)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(cli.signal, "signal", lambda *_: None)
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exit_info:
        cli._up(None)
    assert exit_info.value.code == 1
    assert "provider" in caplog.text
    assert "exited with code -9" in caplog.text
