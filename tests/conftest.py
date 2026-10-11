import os

os.environ["LLM_MODE"] = "mock"
os.environ["TRANSPORT"] = "inproc"
os.environ["SUMER_OBSERVE"] = "false"

import pytest

from sumer.protocol import provision_identities


@pytest.fixture(autouse=True)
def identities(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMER_IDENTITY_DIR", str(tmp_path / "keys"))
    return provision_identities()
