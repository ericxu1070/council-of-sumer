import pytest

from sumer.llm import parse_json


@pytest.mark.parametrize("fenced", [False, True])
def test_strict_json_trims_nested_keys_but_preserves_values(fenced):
    text = ('{" proposal\\u00a0": {"land_delta": 30, "feed\\u00a0": 1800, '
            '"\\u2003plant\\t": 0}, "speech ": " keep this\\u00a0", '
            '"accept": ["plan-id "]}')
    if fenced:
        text = f"```json\n{text}\n```"
    assert parse_json(text, strict=True) == {
        "proposal": {"land_delta": 30, "feed": 1800, "plant": 0},
        "speech": " keep this\u00a0", "accept": ["plan-id "],
    }


@pytest.mark.parametrize("text", [
    '{"feed": 1800, "feed\\u00a0": 2000}',
    '{"proposal": {"feed\\u00a0": 1800, "feed": 2000}}',
    '{"feed": 1800, "feed": 2000}',
])
def test_strict_json_rejects_duplicate_keys(text):
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        parse_json(text, strict=True)


@pytest.mark.parametrize("text", [
    '[{"feed": 1800}]',
    '{"proposal": {"feed": 1800},',
    'Here is the plan: {"feed": 1800}',
])
def test_strict_json_still_requires_a_complete_object(text):
    with pytest.raises(ValueError):
        parse_json(text, strict=True)
