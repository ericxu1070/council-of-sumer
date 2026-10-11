import json
from dataclasses import replace

import pytest

from sumer.game import (
    YEARS,
    Decision,
    InvalidBudget,
    State,
    YearRolls,
    budget_calculation,
    legalize,
    new_game,
    rating,
    step,
)


def rolls(**kwargs):
    return replace(YearRolls(19, 4, 2, 3, False), **kwargs)


def test_same_seed_same_dice():
    assert new_game(3)[1] == new_game(3)[1]
    assert new_game(3)[1] != new_game(4)[1]


@pytest.mark.parametrize("decision", [
    Decision(10000, 0, 0), Decision(-1001, 0, 0), Decision(0, -1, 0),
    Decision(0, 2801, 0), Decision(0, 2000, 1001), Decision(0, 2800, 2),
    Decision(0, 20.5, 1), Decision(True, 0, 0),
])
def test_invalid_budgets_are_rejected_without_altering_state(decision):
    state = State()
    snapshot = state.public()
    with pytest.raises(InvalidBudget):
        legalize(state, decision)
    assert state.public() == snapshot


def test_integer_accounting_rats_precede_harvest():
    state = State()
    decision = Decision(0, 2000, 501)
    assert legalize(state, decision) is decision
    nxt = step(state, decision, rolls())
    remainder = 2800 - 2000 - 250
    assert nxt.last_report["rats"] == remainder // 2
    assert nxt.grain == remainder - remainder // 2 + 501 * 4
    assert nxt.last_report["starved"] == 0
    assert type(nxt.grain) is int


def test_seed_budget_feedback_contains_rejected_amounts_and_shortfall():
    state = State(population=52, grain=1150, land_price=26)
    decision = Decision(0, 1040, 520)
    with pytest.raises(InvalidBudget, match="Insufficient grain after feeding for seed") as exc:
        legalize(state, decision)
    calculation = json.loads(str(exc.value).split("Budget calculation: ")[1])
    assert calculation["proposal"] == decision.as_dict()
    assert calculation["grain_after_feeding"] == 110
    assert calculation["seed_bushels"] == 260
    assert calculation["grain_shortfall_bushels"] == 150
    assert calculation["max_plant_after_trade_and_feeding"] == 221
    assert legalize(state, Decision(0, 1040, 221))
    with pytest.raises(InvalidBudget):
        legalize(state, Decision(0, 1040, 222))


@pytest.mark.parametrize("land_delta,feed,plant", [(5, 1040, 520), (-6, 1040, 520), (0, 1040, 1000)])
def test_budget_calculation_covers_trades_and_all_shortfalls(land_delta, feed, plant):
    state = State(population=52, grain=1150, land_price=26)
    decision = Decision(land_delta, feed, plant)
    calculation = budget_calculation(state, decision)
    assert calculation["grain_after_seed"] == 1150 - land_delta * 26 - feed - plant // 2
    assert calculation["workforce_shortfall_acres"] == max(0, plant - 520)
    assert calculation["grain_shortfall_bushels"] == max(0, -calculation["grain_after_seed"])


def test_immigration_uses_starting_population_despite_starvation():
    nxt = step(State(), Decision(0, 1800, 500), rolls(plague=True))
    expected = 3 * (20 * nxt.acres + nxt.grain) // (100 * 100) + 1
    assert nxt.last_report["starved"] == 10
    assert nxt.last_report["immigrants"] == expected
    assert nxt.population == (90 + expected) // 2
    assert nxt.plague_total == 90 + expected - nxt.population


def test_overthrow_precedes_arrivals_and_plague():
    nxt = step(State(), Decision(0, 1000, 500), rolls(plague=True))
    assert nxt.impeached and nxt.over
    assert nxt.last_report["immigrants"] == nxt.last_report["plague_deaths"] == 0
    assert nxt.population == 50


def test_depopulation_terminates_and_final_year_applies_plague():
    assert step(State(), Decision(0, 0, 0), rolls()).status == "depopulated"
    end = step(State(year=YEARS), Decision(0, 2000, 500), rolls(plague=True))
    assert end.year == YEARS + 1 and end.status == "completed"
    assert end.last_report["immigrants"] > 0 and end.last_report["plague_deaths"] > 0
    assert rating(State(year=3, starved_pct_sum=10))["avg_starved_pct"] == 5
