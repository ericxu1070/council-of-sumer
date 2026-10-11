"""Hamurabi (1968) rules, made deterministic per seed.

Every random roll for a whole game is drawn up front from the seed, so two
policies played on the same seed face exactly the same harvests, rats, and
plagues. Future rolls are private to the engine.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

FOOD_PER_PERSON = 20  # bushels a person eats per year
ACRES_PER_BUSHEL = 2  # one bushel of seed plants two acres
ACRES_PER_WORKER = 10  # one person can tend ten acres
PLAGUE_CHANCE = 0.15
IMPEACH_STARVED_FRACTION = 0.45
YEARS = 10
RULE_VERSION = "hamurabi-integer-v2"


@dataclass(frozen=True)
class YearRolls:
    """The hidden dice for one year. Advisors never see these directly."""

    land_price: int  # bushels per acre during this year's trading
    yield_per_acre: int  # 1..5
    rat_divisor: int  # 1..5, rats eat stores / divisor when it is even
    immigration_factor: int  # 1..5
    plague: bool


@dataclass
class State:
    year: int = 1
    population: int = 100
    grain: int = 2800
    acres: int = 1000
    land_price: int = 19
    starved_total: int = 0
    starved_pct_sum: float = 0.0
    plague_total: int = 0
    impeached: bool = False
    last_report: dict = field(default_factory=dict)

    @property
    def over(self) -> bool:
        return self.impeached or self.population <= 0 or self.year > YEARS

    @property
    def status(self) -> str:
        if self.population <= 0:
            return "depopulated"
        if self.impeached:
            return "overthrown"
        return "completed" if self.year > YEARS else "running"

    def public(self) -> dict:
        d = asdict(self)
        d["acres_per_person"] = round(self.acres / max(self.population, 1), 2)
        return d


@dataclass(frozen=True)
class Decision:
    """One year's orders. land_delta < 0 sells land, > 0 buys land."""

    land_delta: int
    feed: int
    plant: int

    def as_dict(self) -> dict:
        return {"land_delta": self.land_delta, "feed": self.feed, "plant": self.plant}


def roll_game(seed: int) -> list[YearRolls]:
    rng = random.Random(seed)
    return [
        YearRolls(
            land_price=rng.randint(17, 26),
            yield_per_acre=rng.randint(1, 5),
            rat_divisor=rng.randint(1, 5),
            immigration_factor=rng.randint(1, 5),
            plague=rng.random() < PLAGUE_CHANCE,
        )
        for _ in range(YEARS + 1)
    ]


def new_game(seed: int) -> tuple[State, list[YearRolls]]:
    rolls = roll_game(seed)
    return State(land_price=rolls[0].land_price), rolls


class InvalidBudget(ValueError):
    """A public rule error; the author must revise the budget."""


def legalize(state: State, d: Decision) -> Decision:
    """Validate without altering any action (retains the existing API name)."""
    if state.over:
        raise InvalidBudget("The reign has ended.")
    if any(type(v) is not int for v in (d.land_delta, d.feed, d.plant)):
        raise InvalidBudget("Land, feeding, and planting must be whole numbers.")
    if d.feed < 0 or d.plant < 0:
        raise InvalidBudget("Feeding and planting cannot be negative.")
    acres = state.acres + d.land_delta
    grain = state.grain - d.land_delta * state.land_price
    if acres < 0:
        raise InvalidBudget("Cannot sell more land than the city owns.")
    if grain < 0:
        raise InvalidBudget("Insufficient grain to buy that land.")
    if d.feed > grain:
        raise InvalidBudget("Insufficient grain after trading to feed that amount.")
    if d.plant > acres:
        raise InvalidBudget("Cannot plant more acres than the city owns after trading.")
    if d.plant > state.population * ACRES_PER_WORKER:
        raise InvalidBudget("Each resident can cultivate at most ten acres.")
    if d.plant // ACRES_PER_BUSHEL > grain - d.feed:
        raise InvalidBudget("Insufficient grain after feeding for seed.")
    return d


def step(state: State, d: Decision, rolls: YearRolls) -> State:
    """Apply one year. Returns a new State with a report of what happened."""
    d = legalize(state, d)
    grain = state.grain - d.land_delta * state.land_price - d.feed - d.plant // ACRES_PER_BUSHEL
    acres = state.acres + d.land_delta

    harvest = d.plant * rolls.yield_per_acre
    rats = grain // rolls.rat_divisor if rolls.rat_divisor % 2 == 0 else 0
    grain = grain - rats + harvest

    # BASIC calculates arrivals against the starting population, before starvation.
    pending_arrivals = (rolls.immigration_factor * (20 * acres + grain)) // (
        state.population * 100
    ) + 1

    fed = d.feed // FOOD_PER_PERSON
    starved = max(0, state.population - fed)
    population = state.population - starved
    impeached = starved * 100 > 45 * state.population
    # An overthrow ends before the next report applies arrivals and plague.
    immigrants = 0 if impeached else pending_arrivals
    population += immigrants
    plague_deaths = population - population // 2 if rolls.plague and not impeached else 0
    population -= plague_deaths

    return State(
        year=state.year + 1,
        population=population,
        grain=grain,
        acres=acres,
        land_price=state.land_price,  # advance() sets next year's price
        starved_total=state.starved_total + starved,
        starved_pct_sum=state.starved_pct_sum + 100 * starved / max(state.population, 1),
        plague_total=state.plague_total + plague_deaths,
        impeached=impeached,
        last_report={
            "decision": d.as_dict(),
            "harvest": harvest,
            "yield_per_acre": rolls.yield_per_acre,
            "rats": rats,
            "starved": starved,
            "immigrants": immigrants,
            "pending_arrivals": pending_arrivals,
            "plague_deaths": plague_deaths,
        },
    )


def rating(state: State) -> dict:
    """The original game's end-of-reign verdict, plus a single numeric score."""
    years = max(state.year - 1, 1)
    avg_starved = state.starved_pct_sum / years
    acres_pp = state.acres / max(state.population, 1)
    # The original 1968 thresholds: average starvation and acres per person.
    if state.population <= 0:
        verdict, why = "unsuccessful", "the city is depopulated"
    elif state.impeached:
        verdict, why = "Nero", "the people overthrew the council"
    elif avg_starved > 33 or acres_pp < 7:
        verdict, why = "Nero", _why(avg_starved, acres_pp, 33, 7)
    elif avg_starved > 10 or acres_pp < 9:
        verdict, why = "Henry VIII", _why(avg_starved, acres_pp, 10, 9)
    elif avg_starved > 3 or acres_pp < 10:
        verdict, why = "fair", _why(avg_starved, acres_pp, 3, 10)
    else:
        verdict, why = "Jefferson", "few starved and every person has 10+ acres"
    wealth = state.grain + state.acres * 20
    score = (
        state.population * 10
        + wealth / 100
        - state.starved_total * 15
        - (2000 if state.impeached else 0)
    )
    return {
        "verdict": verdict,
        "verdict_reason": why,
        "avg_starved_pct": round(avg_starved, 2),
        "acres_per_person": round(acres_pp, 2),
        "population": state.population,
        "grain": state.grain,
        "acres": state.acres,
        "starved_total": state.starved_total,
        "plague_total": state.plague_total,
        "status": state.status,
        "completed_years": min(state.year - 1, YEARS),
        "impeached": state.impeached,
        "score": round(score, 1),
    }


def _why(avg_starved: float, acres_pp: float, starve_max: float, acres_min: float) -> str:
    reasons = []
    if avg_starved > starve_max:
        reasons.append(f"{avg_starved:.0f}% starved on average (limit {starve_max}%)")
    if acres_pp < acres_min:
        reasons.append(f"only {acres_pp:.1f} acres per person (needs {acres_min})")
    return " and ".join(reasons)


def advance(state: State, d: Decision, rolls: list[YearRolls]) -> State:
    """Play one year and set next year's land price from the pre-rolled dice."""
    nxt = step(state, d, rolls[state.year - 1])
    if nxt.year - 1 < len(rolls):
        nxt.land_price = rolls[nxt.year - 1].land_price
    return nxt
