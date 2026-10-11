import os

os.environ["LLM_MODE"] = "bayleaf"
os.environ["TRANSPORT"] = "inproc"
os.environ["SUMER_OBSERVE"] = "false"

import json

import pytest

from sumer import llm


@pytest.fixture
def llm_reply(monkeypatch):
    """Stub only model I/O; the agent still parses and validates its response."""
    payload = {"back": None, "proposal": {"land_delta": 0, "feed": 2000, "plant": 1000},
               "accept": [], "speech": "Farmer, let us feed the city.",
               "thought": "Keeping the city fed supports my mandate."}
    async def chat(*args, **kwargs):
        return llm.Reply(json.dumps(payload), "test-model", False, 0.1, 0.0)
    monkeypatch.setattr(llm, "chat", chat)
    return payload
