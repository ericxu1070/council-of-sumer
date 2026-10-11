"""The narrator: one or two plain-English sentences about each year.

Built from templates, so it is deterministic and needs no LLM. Written for a
non-technical audience: what the council decided, why in one phrase, and what
happened to the city.
"""

from __future__ import annotations

from sumer.advisors import short
from sumer.deliberation import YearResult
from sumer.game import FOOD_PER_PERSON, State

NEWS_PHRASE = {
    ("harvest", "poor"): "Warned of locusts",
    ("harvest", "rich"): "Expecting a rich flood",
    ("harvest", "ordinary"): "Told to expect an ordinary harvest",
    ("rats", "yes"): "With rats loose in the granary",
    ("rats", "no"): "With the granary free of rats",
    ("plague", "yes"): "With plague coming up the river",
    ("plague", "no"): "Hearing of no sickness abroad",
    ("market", ""): "With word of land prices from Lagash",
}


def _news_phrase(news: list[dict]) -> str | None:
    if not news:
        return None
    n = news[-1]
    kind, text = n.get("kind", ""), n.get("text", "").lower()
    sub = ""
    if kind == "harvest":
        sub = "poor" if "poor" in text else "rich" if "strong" in text else "ordinary"
    elif kind in ("rats", "plague"):
        sub = "no" if ("free" in text or "no sickness" in text) else "yes"
    return NEWS_PHRASE.get((kind, sub))


def _what(yr: YearResult, before: State) -> str:
    """What the plan does, in words: 'planted every field', 'bought land', ..."""
    d = yr.plan
    bits = []
    if d.land_delta > 0:
        bits.append("bought land")
    elif d.land_delta < 0:
        bits.append("sold land")
    need = before.population * FOOD_PER_PERSON
    if d.feed >= 1.05 * need:
        bits.append("gave everyone extra rations")
    elif d.feed < 0.9 * need:
        bits.append("cut the rations")
    elif d.feed < need:
        bits.append("trimmed the rations")
    sowable = min(before.acres + d.land_delta, before.population * 10)
    share = d.plant / max(sowable, 1)
    if share >= 0.95:
        bits.append("planted every field")
    elif share >= 0.6:
        bits.append("planted most of the fields")
    else:
        bits.append("left many fields fallow")
    if len(bits) > 2:  # land and rations say more than "planted every field"
        bits = bits[:2]
    return " and ".join(bits)


def _decision(yr: YearResult, before: State, opener: str | None) -> str:
    """The decision clause. `opener` is a news phrase that already leads the sentence."""
    what = _what(yr, before)
    who = short(yr.adopted_author)
    if yr.outcome == "consensus":
        if opener:
            return f"{opener}, the whole council backed the {who}'s plan, so the city {what}"
        lead = "the whole council quickly agreed" if yr.rounds <= 2 else (
            "after a long debate, the whole council agreed")
        return f"{lead} on the {who}'s plan, so the city {what}"
    if yr.outcome == "majority":
        lead = opener or ("after a heated debate" if yr.rounds >= 3 else "after a short debate")
        over = _over(yr.dissent)
        return f"{lead}, the council sided with the {who}{over}, so the city {what}"
    body = _split(yr, who, what)
    return f"{opener}, {body}" if opener else body


def _over(dissent: list[str]) -> str:
    names = [short(a) for a in dissent]
    if not names:
        return ""
    if len(names) == 1:
        return f" over the {names[0]}'s objection"
    if len(names) == 2:
        return f" over the objections of the {names[0]} and the {names[1]}"
    return f" over the objections of {_NUMBER.get(len(names), len(names))} advisors"


_NUMBER = {3: "three", 4: "four", 5: "five", 6: "six"}


def _split(yr: YearResult, who: str, what: str) -> str:
    if yr.outcome == "runoff":
        return (f"the council split down the middle and went to a runoff, where the {who}'s "
                f"plan won, and the city {what}")
    if yr.human_choice is None:
        return f"the council deadlocked, so the safe fallback plan was used and the city {what}"
    if yr.human_choice == "safe":
        return f"the council deadlocked, so the human ruler chose the safe plan, and the city {what}"
    return (f"the council deadlocked, so the human ruler chose the {who}'s plan, and the city "
            f"{what}")


def _happened(before: State, after: State) -> str:
    r = after.last_report
    y = r.get("yield_per_acre", 3)
    harvest = "poor" if y <= 2 else "rich" if y >= 4 else "ordinary"
    clauses = [f"The harvest was {harvest}"]
    # Keep it short: name only the worst thing that happened.
    if r.get("starved"):
        n = r["starved"]
        clauses.append(f"{n} {'person' if n == 1 else 'people'} starved")
    elif r.get("plague_deaths"):
        clauses.append("plague swept the streets")
    if after.impeached:
        clauses.append("the angry people overthrew the council")
        return _join(clauses) + "."
    pop0, pop1 = before.population, after.population
    if pop1 > pop0:
        tail = f"the city grew to {pop1} people"
    elif pop1 < pop0:
        tail = f"the city shrank to {pop1} people"
    else:
        tail = f"the city held at {pop1} people"
    clauses.append(tail)
    return _join(clauses) + "."


def _join(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return clauses[0]
    return ", ".join(clauses[:-1]) + ", and " + clauses[-1]


def summarize(yr: YearResult, before: State, after: State) -> str:
    """Year N. <decision sentence>. <what happened sentence>."""
    decision = _decision(yr, before, _news_phrase(yr.news))
    decision = decision[0].upper() + decision[1:]
    return f"Year {yr.year}. {decision}. {_happened(before, after)}"
