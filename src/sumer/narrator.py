"""The narrator: plain-English lines about each year.

Built from templates, so it is deterministic and needs no LLM. Written for a
non-technical audience. `summarize` is the written record of the year: what the
council decided, why in one phrase, and what happened to the city. `year_speech`
is what is heard at the end of the year: the winning advisor states the plan in
their own voice, then the narrator reads one headline number.
"""

from __future__ import annotations

from sumer.advisors import ADVISORS, plain_words, short
from sumer.deliberation import YearResult, voting_rules
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


def _decision(yr: YearResult, before: State, advisors=None) -> str:
    roster = ADVISORS if advisors is None else advisors
    total = len(roster)
    total_words = _NUMBER.get(total, str(total))
    who = short(yr.adopted_author, roster)
    did = _did(yr, before)
    to_do = f" to {did}" if did else ""
    if yr.outcome == "consensus":
        return f"All {total_words} agreed on the {who}'s plan{to_do}"
    if yr.outcome == "majority":
        n = total - len(yr.dissent)
        return f"{_NUMBER.get(n, str(n)).capitalize()} of {total_words} sided with the {who}{to_do}"
    if yr.outcome == "runoff":
        count = sorted((yr.runoff or {}).get("count", {}).values(), reverse=True)
        score = (f", {_NUMBER.get(count[0], str(count[0]))}"
                 f" to {_NUMBER.get(count[1], str(count[1]))}" if len(count) == 2 else "")
        floor = voting_rules(total)["runoff_floor"]
        why = (f"Only {_NUMBER.get(floor, str(floor))} of {total_words} backed one plan"
               if (yr.runoff or {}).get("trigger") == "three"
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


def summarize(yr: YearResult, before: State, after: State, advisors=None) -> str:
    """Year N. <who won and what it does>. <what happened>. About 20 words."""
    return f"Year {yr.year}. {_decision(yr, before, advisors)}. {_happened(before, after)}"


# ---- the year-end speech: the winner states the plan, the narrator gives one number ----

_WON = {
    "consensus": "The whole council stands with me.",
    "majority": "The council has backed my plan.",
    "runoff": "My plan won the runoff.",
    "escalated": "You chose my plan.",
}


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def headline(before: State, after: State) -> str:
    """The single most dramatic change of the year, as one short sentence."""
    r = after.last_report
    starved, plague = r.get("starved", 0), r.get("plague_deaths", 0)
    if after.impeached:
        return f"{_count(starved, 'soul', 'souls')} starved, and the people overthrew the council."
    if after.population <= 0:
        return "No one is left in the city."
    if starved or plague:  # deaths outrank every other change
        if starved >= plague:
            return f"{_count(starved, 'soul', 'souls')} starved."
        return f"Plague took {_count(plague, 'soul', 'souls')}."
    pop = after.population - before.population
    grain = after.grain - before.grain
    land = after.acres - before.acres
    rats = r.get("rats", 0)
    options = [
        (abs(pop) / max(before.population, 1),
         f"Population {'rose' if pop > 0 else 'fell'} to {after.population:,}."),
        (abs(grain) / max(before.grain, 1),
         f"The granary {'grew' if grain > 0 else 'shrank'} by {_count(abs(grain), 'bushel', 'bushels')}."),
        (abs(land) / max(before.acres, 1),
         f"We {'bought' if land > 0 else 'sold'} {_count(abs(land), 'acre', 'acres')} of land."),
        # Rats are vivid: rank them a little above an equal change in the granary.
        (1.2 * rats / max(before.grain, 1), f"Rats ate {_count(rats, 'bushel', 'bushels')}."),
    ]
    score, text = max(options, key=lambda o: o[0])
    return text if score > 0 else f"The city held steady at {_count(after.population, 'soul', 'souls')}."


def year_speech(yr: YearResult, before: State, after: State, advisors=None) -> dict:
    """Who speaks at the end of the year, what they say, and the headline that follows.

    The winning advisor states the adopted plan in the first person, in their own
    voice; when no advisor won (the safe fallback), the narrator states it instead."""
    roster = ADVISORS if advisors is None else advisors
    words = plain_words(yr.plan, before)
    winner = roster.get(yr.adopted_author) if yr.adopted_id != "safe" else None
    if winner is None:
        speaker, voice = "narrator", "narrator"
        text = f"The council could not agree, so the safe plan holds. The city will {words}."
    else:
        speaker, voice = winner.id, winner.tts_voice or winner.id
        lead = _WON.get(yr.outcome, "My plan carried the council.")
        text = f"{lead} We will {words}."
    return {"speaker": speaker, "voice": voice, "text": text, "headline": headline(before, after)}
