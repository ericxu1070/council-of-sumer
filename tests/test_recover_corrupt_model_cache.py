import json
from dataclasses import replace

import pytest

from sumer import llm


@pytest.mark.parametrize("cached", ['{', '{}', '[]', '{"content": null}', '{"content": ""}'])
async def test_bad_cache_refetches(monkeypatch, tmp_path, cached):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_cache=True))
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "spent_usd", lambda: 0)
    path = llm._cache_path([])
    path.parent.mkdir()
    path.write_text(cached)
    async def call(*args):
        return llm.Reply("{}", "test", False, 0, 0)
    monkeypatch.setattr(llm, "_call", call)
    reply = await llm.chat([])
    assert not reply.cached
    assert json.loads(path.read_text()) == {"content": "{}"}


async def test_valid_cache_still_works_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_cache=True))
    monkeypatch.setattr(llm, "enabled", lambda: False)
    path = llm._cache_path([])
    path.parent.mkdir()
    path.write_text('{"content":"{}"}')
    assert (await llm.chat([])).cached
