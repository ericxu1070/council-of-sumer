from dataclasses import replace

import httpx
import pytest

from sumer import llm


@pytest.mark.parametrize("status, attempts, exception", [
    (404, 1, llm.LLMRejected),
    (503, 3, llm.LLMFailed),
])
async def test_provider_error_is_explained_and_only_transient_errors_retry(
    monkeypatch, status, attempts, exception,
):
    requests = []
    message = "No endpoints match your data retention policy."

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": message}})

    client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw:
        client(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(llm, "settings", replace(llm.settings,
        llm_mode="bayleaf", llm_api_key="test-key", llm_cache=False,
        llm_base_url="https://example.test/v1"))
    monkeypatch.setattr(llm, "spent_usd", lambda: 0)
    monkeypatch.setattr(llm, "BACKOFF_S", (0, 0))

    with pytest.raises(exception, match=message) as error:
        await llm.chat([{"role": "user", "content": "Return JSON."}])

    assert f"HTTP {status}" in str(error.value)
    assert "test-key" not in str(error.value)
    assert len(requests) == attempts
