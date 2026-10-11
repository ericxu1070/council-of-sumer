"""The narrator: two short plain-English sentences about each year.

Built from templates, so it is deterministic and needs no LLM. Written for a
non-technical audience: what the council decided, why in one phrase, and what
happened to the city.
"""

from __future__ import annotations

from sumer.advisors import short
from sumer.deliberation import YearResult
from sumer.game import FOOD_PER_PERSON, State

_NUMBER = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def _did(yr: YearResult, before: State) -> str:
    """What the plan did, only when it is news: trading land, changing rations,
    leaving fields fallow. Planting every field is the usual case, so it goes unsaid."""
    d = yr.plan
    bits = []
    if d.land_delta > 0:
        bits.append("buy land")
    elif d.land_delta < 0:
        bits.append("sell land")
    need = before.population * FOOD_PER_PERSON
    if d.feed >= 1.05 * need:
        bits.append("give extra rations")
    elif d.feed < need:
        bits.append("cut the rations")
    sowable = min(before.acres + d.land_delta, before.population * 10)
    if d.plant < 0.6 * max(sowable, 1):
        bits.append("leave fields fallow")
    return " and ".join(bits[:2])


def _decision(yr: YearResult, before: State) -> str:
    who = short(yr.adopted_author)
    did = _did(yr, before)
    to_do = f" to {did}" if did else ""
    if yr.outcome == "consensus":
        return f"All seven agreed on the {who}'s plan{to_do}"
    if yr.outcome == "majority":
        n = 7 - len(yr.dissent)
        return f"{_NUMBER[n].capitalize()} of seven sided with the {who}{to_do}"
    if yr.outcome == "runoff":
        count = sorted((yr.runoff or {}).get("count", {}).values(), reverse=True)
        score = f", {_NUMBER[count[0]]} to {_NUMBER[count[1]]}" if len(count) == 2 else ""
        why = ("Only three of seven backed one plan" if (yr.runoff or {}).get("trigger") == "three"
               else "The top two plans were tied")
        return f"{why}, so it went to a runoff. The {who} won{score}"
    if yr.human_choice is None:
        return "The council deadlocked, so the safe plan was used"
    if yr.human_choice == "safe":
        return "The council deadlocked, so you chose the safe plan"
    return f"The council deadlocked, so you chose the {who}'s plan"


def _happened(before: State, after: State) -> str:
    r = after.last_report
    y = r.get("yield_per_acre", 3)
    harvest = "A poor harvest" if y <= 2 else "A rich harvest" if y >= 4 else "An ordinary harvest"
    if after.impeached:
        return f"{harvest}, and the angry people overthrew the council."
    if r.get("starved"):
        n = r["starved"]
        harm = f"{n} {'person' if n == 1 else 'people'} starved"
    elif r.get("plague_deaths"):
        harm = "plague swept the streets"
    else:
        harm = ""
    pop0, pop1 = before.population, after.population
    change = "grew to" if pop1 > pop0 else "shrank to" if pop1 < pop0 else "held at"
    tail = f"the city {change} {pop1} people"
    return f"{harvest}; {harm}, and {tail}." if harm else f"{harvest}, and {tail}."


def summarize(yr: YearResult, before: State, after: State) -> str:
    """Year N. <who won and what it does>. <what happened>. About 20 words."""
    return f"Year {yr.year}. {_decision(yr, before)}. {_happened(before, after)}"
