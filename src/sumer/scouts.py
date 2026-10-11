"""User-selected events change the hidden rolls and become public council news."""
from dataclasses import replace

from sumer.game import YearRolls

SCOUT_OPTIONS = [
    {"id": "plague", "label": "Cause a plague", "detail": "Halve the population this year."},
    {"id": "rats", "label": "Introduce rats", "detail": "Rats take half the remaining stores before harvest."},
    {"id": "good_harvest", "label": "Good harvest", "detail": "Yield 5 bushels per planted acre."},
    {"id": "bad_harvest", "label": "Bad harvest", "detail": "Yield 1 bushel per planted acre."},
    {"id": "low_price", "label": "Low land price", "detail": "Land costs 17 bushels per acre next year."},
    {"id": "high_price", "label": "High land price", "detail": "Land costs 26 bushels per acre next year."},
    {"id": "skip", "label": "Skip", "detail": "Keep the vote and continue without a new event."},
]
SCOUT_IDS = {option["id"] for option in SCOUT_OPTIONS}


def apply_scout(rolls: list[YearRolls], year: int, choice: str) -> dict | None:
    if choice not in SCOUT_IDS:
        raise ValueError("Choose a listed scout event or skip.")
    if choice == "skip":
        return None
    index = year - 1
    current = rolls[index]
    if choice == "plague":
        rolls[index] = replace(current, plague=True)
        text, beliefs = "A plague will halve the population this year.", {"plague_chance": 1.0}
    elif choice == "rats":
        rolls[index] = replace(current, rat_divisor=2)
        text = "Rats will eat half the stores left after trading, food, and seed, before harvest."
        beliefs = {"rat_chance": 1.0, "rat_fraction": 0.5}
    elif choice in ("good_harvest", "bad_harvest"):
        amount = 5 if choice == "good_harvest" else 1
        rolls[index] = replace(current, yield_per_acre=amount)
        unit = "bushel" if amount == 1 else "bushels"
        text = f"The harvest will yield {amount} {unit} per planted acre this year."
        beliefs = {"expected_yield": float(amount)}
    else:
        amount = 17 if choice == "low_price" else 26
        rolls[index + 1] = replace(rolls[index + 1], land_price=amount)
        text = f"Land will cost {amount} bushels per acre next year."
        beliefs = {"next_price": float(amount)}
    return {"kind": choice, "text": text, "beliefs": beliefs, "injected": True}
