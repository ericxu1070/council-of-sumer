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

VILLAGER_MANDATE = (
    "Maximize the expected final numeric score over the whole reign: "
    "10 * surviving people + (remaining bushels + 20 * final acres) / 100 "
    "- 15 * total people starved - (2000 if overthrown else 0). "
    "Balance all five terms and future consequences, "
    "including workers, immigration, harvests, rats, and plague. You are willing to let "
    "people die when the expected score gain outweighs the loss of survivors and the "
    "starvation penalty. More than 45% starvation in one year ends the reign immediately; "
    "account for the overthrow penalty and lost future opportunities. Favor neither "
    "starvation nor survival for its own sake. The narrative verdict is separate from "
    "your numeric-score objective."
)

COMPROMISE_GUIDANCE = (
    "Negotiate concrete concessions on food, seed, land, and reserves. Explain how a "
    "tradeoff helps the expected final score, listen to objections, revise your proposal, "
    "and support acceptable alternatives. Form alliances with councilors who share a "
    "near-term goal against a common enemy such as famine, rats, a poor harvest, overthrow, "
    "or a proposal that threatens those goals. Offer reciprocal concessions; do not invent "
    "sabotage or treat a councilor as a permanent enemy. Your bias value controls how "
    "strongly you favor your specialty and resist compromise: 0 means no extra preference "
    "and ready compromise for the score; 0.25 is a mild preference; 1 is an extreme "
    "preference with strong resistance to concessions. Higher bias requires more "
    "persuasion or a larger concession, but still negotiate and seek a workable coalition."
)


def compromise_mandate(mandate: str) -> str:
    return mandate if mandate.endswith(COMPROMISE_GUIDANCE) else f"{mandate} {COMPROMISE_GUIDANCE}"


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
    bias: float = 0.0  # preference intensity and resistance to concessions, from 0 to 1
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
            "bias": self.bias,
        }

    def beliefs_for(self, b: Beliefs) -> Beliefs:
        """Diagnostic forecasts use the shared evidence for every role."""
        return b

    def utility(self, f: Forecast, state: State) -> float:
        """Role-aligned diagnostics; these never enter the model's prompt or votes."""
        if self.id not in ADVISORS:
            return f.wealth
        score = (10 * f.population + (f.grain + 20 * f.acres) / 100
                 - 15 * (state.starved_total + f.starved)
                 - (2000 if f.starved_frac > 0.45 else 0))
        # One-year diagnostic proxy, not a final-reign prediction or voting policy.
        preference = {
            "provider": -25 * f.starved,
            "farmer": f.plant / 5,
            "merchant": f.acres / 5,
            "priest": f.grain / 100,
            "villager": 0,
        }[self.id]
        return score + self.bias * preference


ADVISORS: dict[str, Advisor] = {
    a.id: replace(a, mandate=compromise_mandate(a.mandate))
    for a in [
        Advisor(
            id="provider", title="Provider", short="Provider", bias=0.25,
            mandate=VILLAGER_MANDATE + " Your specialty is keeping everyone fed. Prefer full "
            "rations and reliable food supplies, and argue for selling land or reducing "
            "planting when that protects meals. This is a bias, not an absolute veto: "
            "consider sacrifices and concessions in the context of the whole-reign score.",
            voice="warm, protective, concrete; bargains for meals and households",
            color="#c2577e", emoji="🍞",
        ),
        Advisor(
            id="farmer", title="Farmer", short="Farmer", bias=0.25,
            mandate=VILLAGER_MANDATE + " Your specialty is planting as much grain as possible. "
            "Prefer sowing more workable acres and securing seed and workers for future "
            "harvests. Bargain for planting capacity, while weighing food and reserves "
            "against the whole-reign score rather than planting at any cost.",
            voice="practical, grounded in fields and seed; bargains for productive harvests",
            color="#3f8f4f", emoji="🌾",
        ),
        Advisor(
            id="merchant", title="Merchant", short="Merchant", bias=0.25,
            mandate=VILLAGER_MANDATE + " Your specialty is maximizing total acres owned at "
            "the end of the reign, not acres per person. Favor affordable purchases and "
            "retaining land. Trade temporary sales or food and seed concessions for a "
            "credible route to more final acres, while weighing the whole-reign score.",
            voice="measured, persuasive, commercially shrewd; bargains for lasting acreage",
            color="#c0911e", emoji="💰",
        ),
        Advisor(
            id="priest", title="Priest", short="Priest", bias=0.25,
            mandate=VILLAGER_MANDATE + " Your specialty is maximizing bushels remaining at "
            "the end and keeping grain in store for emergencies. Favor reserves against "
            "famine, poor harvests, and rats, while recognizing stores can be lost to rats. "
            "Negotiate how much to release for meals, seed, or land when that improves "
            "the whole-reign score; hoarding is a preference, not an absolute rule.",
            voice="calm, prudent, persuasive; bargains for emergency stores and future security",
            color="#b5452f", emoji="🏺",
        ),
        Advisor(
            id="villager", title="Villager", short="Villager", bias=0.0,
            mandate=VILLAGER_MANDATE,
            voice="plainspoken, pragmatic; weighs the whole city's outcome and brokers bargains",
            color="#8a5cc2", emoji="🧑",
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
    # Stored/public mandates also carry the guidance appended to the user's instructions.
    mandate: str | None = Field(
        default=None, min_length=1, max_length=2000 + len(COMPROMISE_GUIDANCE) + 1,
    )
    bias: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    voice: str | None = Field(default=None, min_length=1, max_length=200)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    emoji: str | None = Field(default=None, min_length=1, max_length=12)

    @model_validator(mode="after")
    def custom_identity(self):
        if self.mandate and len(self.mandate.removesuffix(f" {COMPROMISE_GUIDANCE}")) > 2000:
            raise ValueError("Agent instructions must be at most 2000 characters.")
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
                mandate=compromise_mandate(seat.mandate or base.mandate),
                bias=base.bias if seat.bias is None else seat.bias,
                voice=seat.voice or base.voice,
                color=seat.color or base.color,
                emoji=seat.emoji or base.emoji,
            )
        else:
            roster[seat.id] = Advisor(
                id=seat.id,
                short=seat.short,
                title=seat.title or seat.short,
                mandate=compromise_mandate(seat.mandate),
                bias=0.0 if seat.bias is None else seat.bias,
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
