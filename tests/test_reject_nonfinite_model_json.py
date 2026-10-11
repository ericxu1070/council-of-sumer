import pytest
from sumer.llm import parse_json


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_nonfinite_constants(constant):
    with pytest.raises(ValueError, match="Non-finite"):
        parse_json('{"proposal": {"feed": ' + constant + '}}', strict=True)


def test_finite_json_and_literal_strings_remain_valid():
    assert parse_json('{"value": 2.5, "speech": "NaN"}', strict=True) == {"value": 2.5, "speech": "NaN"}
