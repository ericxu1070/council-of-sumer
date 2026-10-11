from dataclasses import replace

import httpx
import pytest

from sumer import llm


@pytest.mark.parametrize("usage", [{"cost": "NaN"}, {"cost": "Infinity"}, {"cost": -1}, {"cost": "oops"}, [], {"cost": True}])
async def test_invalid_cost_never_enters_ledger(monkeypatch, tmp_path, usage):
    client = httpx.AsyncClient
    def response(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": usage})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(response), **kw))
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_base_url="https://example.test/v1"))
    monkeypatch.setattr(llm, "SPEND_LOG", tmp_path / "spend.log")
    with pytest.raises(llm.LLMRejected, match="cost"):
        await llm._call([], 0.4)
    assert not llm.SPEND_LOG.exists()


async def test_numeric_cost_is_recorded(monkeypatch, tmp_path):
    client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0.02"}})), **kw))
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_base_url="https://example.test/v1"))
    monkeypatch.setattr(llm, "SPEND_LOG", tmp_path / "spend.log")
    assert (await llm._call([], 0.4)).cost_usd == 0.02
    assert llm.spent_usd() == 0.02
