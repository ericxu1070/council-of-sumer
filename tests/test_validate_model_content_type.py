from dataclasses import replace
import httpx
import pytest
from sumer import llm


@pytest.mark.parametrize("content", [[{"text":"{}"}], {"text":"{}"}, 123, "   "])
async def test_bad_content_is_controlled_failure_with_usage(monkeypatch, tmp_path, content):
    client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"cost": 0.02}})), **kw))
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_base_url="https://example.test/v1"))
    monkeypatch.setattr(llm, "SPEND_LOG", tmp_path / "spend.log")
    with pytest.raises(llm.LLMFailed) as exc:
        await llm._call([], 0.4)
    assert exc.value.usage["cost_usd"] == 0.02
    assert llm.spent_usd() == 0.02
