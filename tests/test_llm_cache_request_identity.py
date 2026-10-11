from dataclasses import replace
from sumer import llm


def test_cache_separates_provider_endpoints(monkeypatch):
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_base_url="https://one.test/v1"))
    first = llm._cache_path([])
    monkeypatch.setattr(llm, "settings", replace(llm.settings, llm_base_url="https://two.test/v1"))
    assert llm._cache_path([]) != first


def test_cache_separates_sampling_temperatures():
    assert llm._cache_path([], 0.1) != llm._cache_path([], 0.9)
