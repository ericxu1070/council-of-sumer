"""The four advisors: public mandates, red lines, and how each scores a plan.

Each advisor judges a plan by forecasting next year's city under its current
beliefs and scoring the forecast against its own mandate. Mandates conflict
by design: the General wants land, the Priest wants full granaries, the
Farmer wants every mouth fed, and the Merchant wants wealth at market prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sumer.game import (
    ACRES_PER_BUSHEL,
    ACRES_PER_WORKER,
    FOOD_PER_PERSON,
    PLAGUE_CHANCE,
    Decision,
    State,
    YearRolls,
    legalize,
)


@dataclass(frozen=True)
class Beliefs:
    """What the council believes about this year. Scout reports update it."""

    expected_yield: float = 3.0
    rat_chance: float = 0.4
    rat_fraction: float = 0.375  # mean of 1/2 and 1/4
    plague_chance: float = PLAGUE_CHANCE
    next_price: float = 21.5


@dataclass(frozen=True)
class Forecast:
    grain: float
    acres: int
    population: float
    starved: int
    starved_frac: float
    land_delta: int
    wealth: float


def forecast(state: State, d: Decision, b: Beliefs) -> Forecast:
    d = legalize(state, d)
    grain = state.grain - d.land_delta * state.land_price - d.feed - d.plant // ACRES_PER_BUSHEL
    grain += d.plant * b.expected_yield
    grain -= grain * b.rat_chance * b.rat_fraction
    acres = state.acres + d.land_delta
    starved = max(0, state.population - d.feed // FOOD_PER_PERSON)
    pop = state.population - starved
    if starved == 0:
        pop += 3 * (20 * acres + grain) / max(pop, 1) / 100 + 1
    pop *= 1 - b.plague_chance * 0.5
    return Forecast(
        grain=grain,
        acres=acres,
        population=pop,
        starved=starved,
        starved_frac=starved / max(state.population, 1),
        land_delta=d.land_delta,
        wealth=grain + acres * b.next_price,
    )


@dataclass(frozen=True)
class Advisor:
    id: str
    title: str
    mandate: str
    red_line: str
    voice: str  # how the advisor talks, used by the LLM and the templates
    color: str
    style_words: tuple[str, ...] = field(default_factory=tuple)

    def beliefs_for(self, b: Beliefs) -> Beliefs:
        """The Priest plans for a bad year unless the news says otherwise."""
        if self.id != "priest":
            return b
        return replace(
            b,
            expected_yield=max(1.0, b.expected_yield - 1.5),
            rat_chance=b.rat_chance if b.rat_chance in (0.0, 1.0) else 1.0,
        )

    def utility(self, f: Forecast, state: State) -> float:
        match self.id:
            case "farmer":
                return f.population - 4 * f.starved
            case "priest":
                return f.grain
            case "general":
                return f.acres + 5 * f.population
            case "merchant":
                return f.wealth
        raise ValueError(self.id)

    def violates_red_line(self, f: Forecast, state: State) -> bool:
        match self.id:
            case "farmer":
                return f.starved_frac > 0.10
            case "priest":
                return f.grain < 0.5 * f.population * FOOD_PER_PERSON
            case "general":
                return f.land_delta < -0.10 * state.acres
            case "merchant":
                return False  # the Merchant always has a price
        raise ValueError(self.id)


ADVISORS: dict[str, Advisor] = {
    a.id: a
    for a in [
        Advisor(
            id="farmer",
            title="Voice of the Farmers",
            mandate="Grow the population. Nobody starves.",
            red_line="Will never accept a plan that starves more than 10% of the people.",
            voice="earnest, speaks for families, cites hungry children",
            color="#3f8f4f",
        ),
        Advisor(
            id="priest",
            title="High Priest of Enlil",
            mandate="Keep the temple granaries full against famine. Plans for a bad harvest.",
            red_line="Will never accept a plan that, in a bad year, leaves less than half a year of food in store.",
            voice="solemn, cautious, invokes omens and the gods",
            color="#8a5cc2",
        ),
        Advisor(
            id="general",
            title="General of the Walls",
            mandate="Hold and expand land. More acres, more soldiers.",
            red_line="Will never accept selling more than 10% of our land in one year.",
            voice="blunt, martial, talks about borders and strength",
            color="#b5452f",
        ),
        Advisor(
            id="merchant",
            title="Chief Merchant",
            mandate="Maximize the city's wealth at market prices. Buy land low, sell high.",
            red_line="Has no red line. Everything has a price.",
            voice="quick, transactional, quotes prices",
            color="#c0911e",
        ),
    ]
}
ADVISOR_IDS = list(ADVISORS)


def candidate_plans(state: State) -> list[Decision]:
    """A coarse grid over the legal action space."""
    need = state.population * FOOD_PER_PERSON
    seen: dict[tuple, Decision] = {}
    for lf in (-0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2):
        for ff in (0.6, 0.8, 0.9, 1.0, 1.15):
            for pf in (0.4, 0.7, 1.0):
                land = round(state.acres * lf)
                feed = round(need * ff)
                rough = legalize(state, Decision(land, feed, 10**9))
                plant = round(rough.plant * pf)
                d = legalize(state, Decision(land, feed, plant))
                seen[(d.land_delta, d.feed, d.plant)] = d
    return list(seen.values())


def safe_plan(state: State) -> Decision:
    """The pre-committed fallback when the council cannot decide.

    Feed everyone, selling up to 20% of the land if that is what it takes,
    then sow as much as the remaining grain allows."""
    need = state.population * FOOD_PER_PERSON
    max_plant = min(state.acres, state.population * ACRES_PER_WORKER)
    shortfall = need + max_plant // ACRES_PER_BUSHEL - state.grain
    sell = 0
    if shortfall > 0:
        sell = min(round(0.2 * state.acres), -(-shortfall // state.land_price))
    return legalize(state, Decision(-sell, need, max_plant))


class Scorer:
    """Normalizes each advisor's utility to 0..1 over the plans in play."""

    def __init__(self, state: State, beliefs: Beliefs, extra: list[Decision] = ()):
        self.state = state
        self.beliefs = beliefs
        self.pool = candidate_plans(state) + [legalize(state, d) for d in extra]
        self._range: dict[str, tuple[float, float]] = {}
        for a in ADVISORS.values():
            us = [a.utility(self.forecast(a.id, d), state) for d in self.pool]
            self._range[a.id] = (min(us), max(us))

    def forecast(self, advisor_id: str, d: Decision) -> Forecast:
        return forecast(self.state, d, ADVISORS[advisor_id].beliefs_for(self.beliefs))

    def norm(self, advisor_id: str, d: Decision) -> float:
        a = ADVISORS[advisor_id]
        lo, hi = self._range[advisor_id]
        u = a.utility(self.forecast(advisor_id, d), self.state)
        return 0.5 if hi == lo else max(0.0, min(1.0, (u - lo) / (hi - lo)))

    def vetoes(self, advisor_id: str, d: Decision) -> bool:
        return ADVISORS[advisor_id].violates_red_line(self.forecast(advisor_id, d), self.state)


# ---- New information: scout reports derived from the true hidden dice ----

REPORT_KINDS = ("harvest", "rats", "plague", "market")


def scout_report(kind: str, rolls: YearRolls, next_rolls: YearRolls | None) -> dict:
    """A true report about this year's hidden dice, with its effect on beliefs."""
    match kind:
        case "harvest":
            if rolls.yield_per_acre <= 2:
                return {
                    "kind": kind,
                    "text": "Locusts sighted in the east. Expect a poor harvest, 1 to 2 bushels per acre.",
                    "beliefs": {"expected_yield": 1.5},
                }
            if rolls.yield_per_acre >= 4:
                return {
                    "kind": kind,
                    "text": "The river flooded richly. Expect a strong harvest, 4 to 5 bushels per acre.",
                    "beliefs": {"expected_yield": 4.5},
                }
            return {
                "kind": kind,
                "text": "Field scouts expect an ordinary harvest, about 3 bushels per acre.",
                "beliefs": {"expected_yield": 3.0},
            }
        case "rats":
            if rolls.rat_divisor % 2 == 0:
                frac = 1 / rolls.rat_divisor
                return {
                    "kind": kind,
                    "text": f"Rats are breeding in the granary. They will eat about {round(frac * 100)}% of our stores.",
                    "beliefs": {"rat_chance": 1.0, "rat_fraction": frac},
                }
            return {
                "kind": kind,
                "text": "The cats have done their work. The granary is free of rats this year.",
                "beliefs": {"rat_chance": 0.0},
            }
        case "plague":
            if rolls.plague:
                return {
                    "kind": kind,
                    "text": "Plague has reached Ur and is coming up the river. Half the city may fall sick.",
                    "beliefs": {"plague_chance": 0.9},
                }
            return {
                "kind": kind,
                "text": "Envoys from every neighbor report no sickness this season.",
                "beliefs": {"plague_chance": 0.02},
            }
        case "market":
            price = next_rolls.land_price if next_rolls else 21
            return {
                "kind": kind,
                "text": f"Traders from Lagash say land will sell for {price} bushels an acre next year.",
                "beliefs": {"next_price": float(price)},
            }
    raise ValueError(kind)


def report_for_year(seed: int, year: int, rolls: YearRolls) -> str | None:
    """Pick which report (if any) arrives mid-debate this year. The most
    decision-relevant news wins, so the demo shows information that matters."""
    if rolls.yield_per_acre <= 2 or rolls.yield_per_acre >= 4:
        return "harvest"
    if rolls.rat_divisor in (2, 4):
        return "rats"
    if rolls.plague:
        return "plague"
    return "market" if (seed + year) % 2 == 0 else None


def apply_report(b: Beliefs, report: dict | None) -> Beliefs:
    return replace(b, **report["beliefs"]) if report else b
