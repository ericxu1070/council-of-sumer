"""Hamurabi (1968) rules, made deterministic per seed.

Every random roll for a whole game is drawn up front from the seed, so two
policies played on the same seed face exactly the same harvests, rats, and
plagues. That makes baseline comparisons fair, and it lets a scout report
reveal true (not made up) information about the coming year.
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
    impeached: bool = False
    last_report: dict = field(default_factory=dict)

    @property
    def over(self) -> bool:
        return self.impeached or self.year > YEARS

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


def legalize(state: State, d: Decision) -> Decision:
    """Clamp a proposal into the legal action space instead of rejecting it."""
    max_sell = state.acres
    max_buy = state.grain // state.land_price
    land_delta = max(-max_sell, min(max_buy, int(d.land_delta)))
    grain = state.grain - land_delta * state.land_price
    acres = state.acres + land_delta
    feed = max(0, min(grain, int(d.feed)))
    grain -= feed
    max_plant = min(acres, state.population * ACRES_PER_WORKER, grain * ACRES_PER_BUSHEL)
    plant = max(0, min(max_plant, int(d.plant)))
    return Decision(land_delta, feed, plant)


def step(state: State, d: Decision, rolls: YearRolls) -> State:
    """Apply one year. Returns a new State with a report of what happened."""
    d = legalize(state, d)
    grain = state.grain - d.land_delta * state.land_price - d.feed - d.plant // ACRES_PER_BUSHEL
    acres = state.acres + d.land_delta

    harvest = d.plant * rolls.yield_per_acre
    grain += harvest
    rats = grain // rolls.rat_divisor if rolls.rat_divisor % 2 == 0 else 0
    grain -= rats

    fed = d.feed // FOOD_PER_PERSON
    starved = max(0, state.population - fed)
    population = state.population - starved
    immigrants = (
        int(rolls.immigration_factor * (20 * acres + grain) / max(population, 1) / 100 + 1)
        if starved == 0
        else 0
    )
    population += immigrants
    plague_deaths = population // 2 if rolls.plague else 0
    population -= plague_deaths

    impeached = starved > IMPEACH_STARVED_FRACTION * state.population
    return State(
        year=state.year + 1,
        population=population,
        grain=grain,
        acres=acres,
        land_price=state.land_price,  # advance() sets next year's price
        starved_total=state.starved_total + starved,
        starved_pct_sum=state.starved_pct_sum + 100 * starved / max(state.population, 1),
        impeached=impeached,
        last_report={
            "decision": d.as_dict(),
            "harvest": harvest,
            "yield_per_acre": rolls.yield_per_acre,
            "rats": rats,
            "starved": starved,
            "immigrants": immigrants,
            "plague_deaths": plague_deaths,
        },
    )


def rating(state: State) -> dict:
    """The original game's end-of-reign verdict, plus a single numeric score."""
    years = max(state.year - 1, 1)
    avg_starved = state.starved_pct_sum / years
    acres_pp = state.acres / max(state.population, 1)
    if state.impeached or avg_starved > 33 or acres_pp < 7:
        verdict = "Nero"
    elif avg_starved > 10 or acres_pp < 9:
        verdict = "Henry VIII"
    elif avg_starved > 3 or acres_pp < 10:
        verdict = "fair"
    else:
        verdict = "Jefferson"
    wealth = state.grain + state.acres * 20
    score = (
        state.population * 10
        + wealth / 100
        - state.starved_total * 15
        - (2000 if state.impeached else 0)
    )
    return {
        "verdict": verdict,
        "avg_starved_pct": round(avg_starved, 2),
        "acres_per_person": round(acres_pp, 2),
        "population": state.population,
        "grain": state.grain,
        "acres": state.acres,
        "starved_total": state.starved_total,
        "impeached": state.impeached,
        "score": round(score, 1),
    }


def advance(state: State, d: Decision, rolls: list[YearRolls]) -> State:
    """Play one year and set next year's land price from the pre-rolled dice."""
    nxt = step(state, d, rolls[state.year - 1])
    if nxt.year - 1 < len(rolls):
        nxt.land_price = rolls[nxt.year - 1].land_price
    return nxt
