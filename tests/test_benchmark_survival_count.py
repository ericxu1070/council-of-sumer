from sumer.advisors import ADVISOR_IDS
from sumer.bench import _row
from sumer.chamber import GameResult


def test_depopulation_is_not_survival():
    games = [GameResult(seed=i, policy="lone_ruler", status=status,
        rating={"score": 1, "impeached": status == "overthrown", "starved_total": 0, "population": 0 if status == "depopulated" else 10},
        satisfaction={a: 0 for a in ADVISOR_IDS})
        for i, status in enumerate(["completed", "depopulated", "overthrown", "deadlock", "technical_failure"])]
    row = _row("lone_ruler", games)
    assert row["survived"] == "1/3"
    assert row["deadlocks"] == 1
    assert row["technical_failures"] == 1
