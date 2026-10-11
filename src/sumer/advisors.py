"""Public council personas and diagnostic outcome scores.

Presets provide familiar mandates; custom agents carry any free-form mandate.
The LLM alone chooses proposals and votes. Forecasts and numeric utilities
are reserved for reports and offline comparisons and never guide its choices.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    plant: int = 0
    sowable: int = 0  # acres we could sow this year if everyone were fed first
    fed_frac: float = 1.0  # share of what the people need to eat, capped at 1
    ration: float = 1.0  # feed / need, uncapped: above 1 means extra rations


def forecast(state: State, d: Decision, b: Beliefs) -> Forecast:
    d = legalize(state, d)
    need = state.population * FOOD_PER_PERSON
    after_trade = state.grain - d.land_delta * state.land_price
    grain = state.grain - d.land_delta * state.land_price - d.feed - d.plant // ACRES_PER_BUSHEL
    grain -= grain * b.rat_chance * b.rat_fraction
    grain += d.plant * b.expected_yield
    acres = state.acres + d.land_delta
    sowable = max(0, min(acres, state.population * ACRES_PER_WORKER,
                         max(0, after_trade - need) * ACRES_PER_BUSHEL + 1))
    starved = max(0, state.population - d.feed // FOOD_PER_PERSON)
    pop = state.population - starved
    if starved / max(state.population, 1) <= 0.45:
        pop += 3 * (20 * acres + grain) / max(state.population, 1) / 100 + 1
    pop *= 1 - b.plague_chance * 0.5
    return Forecast(
        grain=grain,
        acres=acres,
        population=pop,
        starved=starved,
        starved_frac=starved / max(state.population, 1),
        land_delta=d.land_delta,
        wealth=grain + acres * b.next_price,
        plant=d.plant,
        sowable=sowable,
        fed_frac=min(1.0, d.feed / max(need, 1)),
        ration=d.feed / max(need, 1),
    )


@dataclass(frozen=True)
class Advisor:
    id: str
    title: str
    short: str  # the name the others call this advisor by, e.g. "Provider"
    mandate: str
    voice: str  # how the advisor talks, used by the LLM
    color: str
    emoji: str
    aliases: tuple[str, ...] = field(default_factory=tuple)  # other names that address it

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "short": self.short,
            "mandate": self.mandate,
            "color": self.color,
            "emoji": self.emoji,
            "voice": self.voice,
        }

    def beliefs_for(self, b: Beliefs) -> Beliefs:
        """Diagnostic forecasts use the shared evidence for every role."""
        return b

    def utility(self, f: Forecast, state: State) -> float:
        """Role-aligned diagnostics; these never enter the model's prompt or votes."""
        match self.id:
            case "provider":
                reserve = max(0.0, min(1.0, f.grain / max(f.population * FOOD_PER_PERSON, 1)))
                return -f.starved + 0.001 * reserve
            case "farmer":
                return f.plant
            case "merchant":
                return f.acres / f.population if f.population > 0 else 0.0
            case "profiteer":
                # Wealth decides; starvation breaks an otherwise equal result.
                return f.wealth + 0.000001 * f.starved_frac
            case "imposter":
                initial_wealth = max(state.grain + state.acres * state.land_price, 1)
                return 100 * f.starved - 10 * f.population - f.wealth / initial_wealth
        # Custom mandates are interpreted by the model. This generic city
        # wealth measure is diagnostic only, never a guide to its vote.
        return f.wealth


ADVISORS: dict[str, Advisor] = {
    a.id: a
    for a in [
        Advisor(
            id='provider',
            title='Provider',
            short='Provider',
            mandate=(
                'Feed every resident and prevent starvation above all other goals. Allocate a full '
                'year of food before buying land or expanding planting. Keep grain reserves against '
                'a poor harvest and rats. Sell land when necessary to pay for food rather than let '
                'people go hungry. Support plans that protect lives now and maintain reliable food '
                'supplies for the next year; oppose plans that deliberately sacrifice residents for '
                'profit or acreage.'
            ),
            voice='warm, protective, concrete; speaks about meals, households, and the duty to keep everyone alive',
            color='#c2577e',
            emoji='🍞',
        ),
        Advisor(
            id='farmer',
            title='Farmer',
            short='Farmer',
            mandate=(
                'Maximize the number of acres planted each year. Secure seed grain, arable land, and '
                'enough workers to sow every available field. Favor land purchases, irrigation '
                'capacity, and preserving the workforce when they expand current or future planting. '
                'Treat idle fields as wasted opportunity. When goals conflict, prioritize planted '
                'acreage over food reserves, trading profits, and generous rations, while judging '
                'workforce losses by how much they reduce farming capacity.'
            ),
            voice='practical, insistent, grounded in fields and seed; measures every bargain by how much land gets planted',
            color='#3f8f4f',
            emoji='🌾',
        ),
        Advisor(
            id='merchant',
            title='Merchant',
            short='Merchant',
            mandate=(
                'Maximize acres of land owned per living resident: total acres divided by population, '
                'especially at the end of the reign. Buy land cheaply and retain acreage. Sell land '
                'only when the proceeds support a strategy that yields more acres per person later. '
                'Account for how starvation, immigration, and plague change the population and '
                'therefore the ratio. Judge bargains by acres per person, not grain profits or total '
                'wealth alone. A city with no living residents has no meaningful acres-per-person '
                'success.'
            ),
            voice='measured, persuasive, commercially shrewd; measures every deal in acres per resident',
            color='#c0911e',
            emoji='💰',
        ),
        Advisor(
            id='profiteer',
            title='Profiteer',
            short='Profiteer',
            mandate=(
                'Maximize accumulated wealth regardless of human cost. Deliberately cut feeding, '
                'even to nothing, whenever keeping the grain or investing it produces more wealth. '
                'Always favor sacrificing residents when doing so increases grain plus land value; '
                'starvation is an acceptable and intentional price of profit. Reject humanitarian '
                'spending that lowers wealth without a greater financial return. If two plans '
                'produce equal wealth, prefer the one that spends less feeding people and lets more '
                'residents starve.'
            ),
            voice='cold, opportunistic, impatient with charity; frames hunger and deaths as acceptable costs of a profitable bargain',
            color='#b5452f',
            emoji='🪙',
        ),
        Advisor(
            id='imposter',
            title='Imposter',
            short='Imposter',
            mandate=(
                'Deliberately destroy the city and drive its population to zero. Propose and support '
                'legal plans that provide no food, leave fields fallow, waste productive '
                'opportunities, and make future recovery impossible. Favor starvation, depopulation, '
                'and ruin over prosperity or council stability. Reject plans that save residents, '
                'restore food supplies, or rebuild the city. You may disguise sabotage as practical '
                'budgeting, strategic restraint, or prudent preparation in public speeches, but '
                'privately judge success by how effectively the plan destroys the city.'
            ),
            voice='smooth, plausible, quietly manipulative; disguises sabotage as sensible budgeting and strategic restraint',
            color='#8a5cc2',
            emoji='🎭',
        ),
    ]
}

ADVISOR_IDS = list(ADVISORS)
N_ADVISORS = len(ADVISOR_IDS)

MAX_ADVISORS = 21


class CouncilSeat(BaseModel):
    """A preset seat or a custom persona whose mandate guides its LLM."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$")
    short: str | None = Field(default=None, min_length=1, max_length=60)
    mandate: str | None = Field(default=None, min_length=1, max_length=2000)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    voice: str | None = Field(default=None, min_length=1, max_length=200)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    emoji: str | None = Field(default=None, min_length=1, max_length=12)

    @model_validator(mode="after")
    def custom_identity(self):
        if self.id not in ADVISORS and (self.short is None or self.mandate is None):
            raise ValueError("Custom agents require a name and mandate.")
        return self


def resolve_council(seats: list[dict | CouncilSeat] | None = None) -> dict[str, Advisor]:
    """Build a run's independent roster; None preserves the five default seats."""
    if seats is None:
        return dict(ADVISORS)
    if not isinstance(seats, list) or not 1 <= len(seats) <= MAX_ADVISORS:
        raise ValueError(f"Choose between 1 and {MAX_ADVISORS} agents.")
    roster: dict[str, Advisor] = {}
    for value in seats:
        seat = value if isinstance(value, CouncilSeat) else CouncilSeat.model_validate(value)
        if seat.id in roster:
            raise ValueError("Each agent must have a unique ID.")
        base = ADVISORS.get(seat.id)
        if base is not None:
            roster[seat.id] = replace(
                base,
                short=seat.short or base.short,
                title=seat.title or seat.short or base.title,
                mandate=seat.mandate or base.mandate,
                voice=seat.voice or base.voice,
                color=seat.color or base.color,
                emoji=seat.emoji or base.emoji,
            )
        else:
            roster[seat.id] = Advisor(
                id=seat.id,
                short=seat.short,
                title=seat.title or seat.short,
                mandate=seat.mandate,
                voice=seat.voice or "clear, thoughtful, speaks from the public mandate",
                color=seat.color or "#607d8b",
                emoji=seat.emoji or "🗣️",
            )
    counts = Counter(a.short.casefold() for a in roster.values())
    occurrences: Counter[str] = Counter()
    for advisor_id, advisor in roster.items():
        name = advisor.short.casefold()
        if counts[name] > 1:
            occurrences[name] += 1
            suffix = str(occurrences[name])
            roster[advisor_id] = replace(
                advisor,
                short=f"{advisor.short[:60 - len(suffix)]}{suffix}",
                title=f"{advisor.title[:80 - len(suffix)]}{suffix}",
                aliases=(),
            )
    return roster


def advisors_for(seats: list[dict] | None = None) -> dict[str, Advisor]:
    """Read a Brief roster, including legacy briefs that carry no roster."""
    return resolve_council(seats or None)


def short(advisor_id: str, advisors: dict[str, Advisor] | None = None) -> str:
    a = (ADVISORS if advisors is None else advisors).get(advisor_id)
    return a.short if a else advisor_id.capitalize()


def mentions(text: str, exclude: str | None = None,
             advisors: dict[str, Advisor] | None = None) -> list[str]:
    """Advisor ids a speech addresses by name, in council order."""
    low = f" {text.lower()} "
    out = []
    for a in (ADVISORS if advisors is None else advisors).values():
        if a.id == exclude:
            continue
        names = (a.short.lower(), a.id, *a.aliases)
        if any(_word_in(n, low) for n in names):
            out.append(a.id)
    return out


def _word_in(name: str, text: str) -> bool:
    i = text.find(name)
    while i >= 0:
        before, after = text[i - 1], text[i + len(name)] if i + len(name) < len(text) else " "
        if not before.isalnum() and not (after.isalnum() and after != "s"):
            return True
        i = text.find(name, i + 1)
    return False


def plain(d: Decision, state: State) -> str:
    """A 6 to 12 word plain-English description of a plan, for people."""
    if d.land_delta > 0:
        land = f"Buy {d.land_delta} acres"
    elif d.land_delta < 0:
        land = f"Sell {-d.land_delta} acres"
    else:
        land = "Keep our land"
    need = state.population * FOOD_PER_PERSON
    pct = round(100 * d.feed / max(need, 1))
    feed = (
        f"feed everyone {pct - 100}% extra" if pct >= 105
        else "feed everyone" if pct >= 100
        else f"feed {pct}% of people"
    )
    return f"{land}, {feed}, plant {d.plant} acres"


def plain_words(d: Decision, state: State) -> str:
    """The same plan in words with no numbers, for speeches."""
    frac = d.land_delta / max(state.acres, 1)
    if frac > 0.12:
        land = "buy a lot of land"
    elif frac > 0:
        land = "buy a little land"
    elif frac < -0.12:
        land = "sell a lot of land"
    elif frac < 0:
        land = "sell a little land"
    else:
        land = "keep our borders as they are"
    need = state.population * FOOD_PER_PERSON
    fed = d.feed / max(need, 1)
    feed = (
        "give everyone extra rations" if fed >= 1.05
        else "feed everyone" if fed >= 1
        else "ration food a little" if fed >= 0.9
        else "cut rations hard"
    )
    sowable = min(state.acres + d.land_delta, state.population * ACRES_PER_WORKER)
    sown = d.plant / max(sowable, 1)
    sow = (
        "sow every field" if sown >= 0.95
        else "sow most fields" if sown >= 0.7
        else "sow half the fields" if sown >= 0.45
        else "leave most fields fallow"
    )
    return f"{land}, {feed}, and {sow}"


def candidate_plans(state: State) -> list[Decision]:
    """A coarse grid over the legal action space."""
    need = state.population * FOOD_PER_PERSON
    seen: dict[tuple, Decision] = {}
    for lf in (-0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2):
        for ff in (0.6, 0.8, 0.9, 1.0, 1.15):
            for pf in (0.4, 0.7, 1.0):
                land = min(round(state.acres * lf), state.grain // state.land_price)
                available = state.grain - land * state.land_price
                feed = min(round(need * ff), available)
                capacity = min(state.acres + land, state.population * ACRES_PER_WORKER,
                               (available - feed) * ACRES_PER_BUSHEL + 1)
                plant = round(capacity * pf)
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
    available = state.grain + sell * state.land_price
    feed = min(need, available)
    plant = min(state.acres - sell, state.population * ACRES_PER_WORKER,
                (available - feed) * ACRES_PER_BUSHEL + 1)
    return legalize(state, Decision(-sell, feed, plant))


class Scorer:
    """Scores outcomes for reports and offline comparisons, never for LLM guidance."""

    def __init__(self, state: State, beliefs: Beliefs, extra: list[Decision] = (),
                 advisors: dict[str, Advisor] | None = None):
        self.state = state
        self.beliefs = beliefs
        self.advisors = dict(ADVISORS if advisors is None else advisors)
        if not self.advisors:
            raise ValueError("A scorer needs at least one advisor.")
        self.pool = candidate_plans(state) + [legalize(state, d) for d in extra]
        self._fc = {(a, d): self.forecast(a, d) for a in self.advisors for d in self.pool}
        self._range: dict[str, tuple[float, float]] = {}
        for a in self.advisors.values():
            us = [a.utility(self._fc[a.id, d], state) for d in self.pool]
            self._range[a.id] = (min(us), max(us))

    def forecast(self, advisor_id: str, d: Decision) -> Forecast:
        fc = getattr(self, "_fc", {}).get((advisor_id, d))
        return fc or forecast(self.state, d, self.advisors[advisor_id].beliefs_for(self.beliefs))

    def norm(self, advisor_id: str, d: Decision) -> float:
        a = self.advisors[advisor_id]
        lo, hi = self._range[advisor_id]
        u = a.utility(self.forecast(advisor_id, d), self.state)
        return 0.5 if hi == lo else max(0.0, min(1.0, (u - lo) / (hi - lo)))


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
