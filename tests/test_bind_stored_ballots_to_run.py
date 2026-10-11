import pytest
from sumer.storage import Store


@pytest.mark.parametrize("kind, key", [("receipt", "responses"), ("ballots", "ballots")])
def test_receipts_cannot_cross_run_boundaries(kind, key):
    journal = Store(":memory:")
    journal.create_run("a", {})
    journal.create_run("b", {})
    good = {"message_id":"good", "run_id":"b"}
    wrong = {"message_id":"wrong", "run_id":"a"}
    with pytest.raises(ValueError, match="run"):
        journal.append("b", kind, {key:[good, wrong]})
    assert journal.events("b") == []
    assert journal.db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0
    journal.append("a", kind, {key:[wrong]})
    journal.append("a", kind, {key:[wrong]})
    assert journal.db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 1
    assert len(journal.events("a")) == 2
    journal.db.close()
