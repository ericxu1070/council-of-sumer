"""The end of each year: the winner states the plan in their own voice, then one headline."""

import pytest
from fastapi.testclient import TestClient

from sumer import voice
from sumer.advisors import ADVISORS, CouncilSeat, resolve_council
from sumer.chamber import play_council
from sumer.deliberation import YearResult
from sumer.game import Decision, State
from sumer.narrator import headline, year_speech
from sumer.storage import Store
from tests.support import unanimous

CUSTOM = {"id": "custom-river", "short": "River Keeper", "mandate": "Protect the river."}


@pytest.fixture(autouse=True)
def no_network_voice(monkeypatch, tmp_path):
    """No API key and an empty cache: voice is unavailable unless a test stubs it."""
    monkeypatch.setattr(voice, "enabled", lambda: False)
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)


def after(before: State, **report) -> State:
    fields = {k: report.pop(k) for k in ("population", "grain", "acres", "impeached") if k in report}
    base = {"population": before.population, "grain": before.grain, "acres": before.acres}
    return State(year=before.year + 1, **{**base, **fields}, last_report=report)


# ---- voices ----

def test_every_built_in_seat_and_custom_choice_has_a_distinct_voice():
    ids = [*voice.DEFAULT_VOICES.values(), *(v for v, _ in voice.CUSTOM_VOICES.values())]
    assert len(ids) == len(set(ids))
    assert set(ADVISORS) < set(voice.DEFAULT_VOICES)
    assert len(voice.CUSTOM_VOICES) == 6
    assert {a.tts_voice for a in ADVISORS.values()} == set(ADVISORS)


def test_unknown_voice_keys_never_reach_the_api():
    assert voice.voice_id("not-a-voice") is None
    assert voice.voice_id(voice.DEFAULT_VOICES["farmer"]) is None  # raw IDs are not keys


@pytest.mark.parametrize("bad", ["narrator", "21m00Tcm4TlvDq8ikWAM", "", "../farmer"])
def test_custom_seat_rejects_voices_outside_the_allowed_set(bad):
    with pytest.raises(ValueError):
        CouncilSeat.model_validate({**CUSTOM, "tts_voice": bad})


def test_custom_seat_keeps_its_chosen_voice_and_round_trips():
    roster = resolve_council([{**CUSTOM, "tts_voice": "elder-woman"}, {"id": "farmer"}])
    assert roster["custom-river"].tts_voice == "elder-woman"
    assert roster["farmer"].tts_voice == "farmer"
    public = [a.public() for a in roster.values()]
    assert [a.public() for a in resolve_council(public).values()] == public


def test_custom_seat_without_a_choice_gets_a_stable_custom_voice():
    first = resolve_council([CUSTOM])["custom-river"].tts_voice
    assert first in voice.CUSTOM_VOICES
    assert resolve_council([CUSTOM])["custom-river"].tts_voice == first


# ---- the headline ----

BEFORE = State(population=100, grain=2800, acres=1000)


@pytest.mark.parametrize(("state", "expected"), [
    (after(BEFORE, starved=80, population=20, impeached=True),
     "80 souls starved, and the people overthrew the council."),
    (after(BEFORE, starved=12, plague_deaths=3, population=90), "12 souls starved."),
    (after(BEFORE, starved=0, plague_deaths=40, population=60), "Plague took 40 souls."),
    (after(BEFORE, starved=1, population=99), "1 soul starved."),
    (after(BEFORE, population=1240), "Population rose to 1,240."),
    (after(BEFORE, grain=4000), "The granary grew by 1,200 bushels."),
    (after(BEFORE, grain=2500, rats=300), "Rats ate 300 bushels."),
    (after(BEFORE, acres=700), "We sold 300 acres of land."),
    (after(BEFORE), "The city held steady at 100 souls."),
])
def test_headline_picks_the_most_dramatic_change(state, expected):
    assert headline(BEFORE, state) == expected


# ---- who speaks ----

def result(outcome="majority", author="farmer", adopted="farmer-1") -> YearResult:
    return YearResult(year=1, outcome=outcome, plan=Decision(0, 2000, 1000),
                      adopted_id=adopted, rounds=1, adopted_author=author)


def test_winner_states_the_plan_in_first_person_in_their_own_voice():
    speech = year_speech(result(), BEFORE, after(BEFORE, population=110))
    assert speech["speaker"] == "farmer" and speech["voice"] == "farmer"
    assert speech["text"] == ("The council has backed my plan. We will keep our borders as they are, "
                              "feed everyone, and sow every field.")
    assert speech["headline"] == "Population rose to 110."
    assert speech["text"].count(".") <= 2


def test_custom_winner_speaks_in_the_voice_it_chose():
    roster = resolve_council([{**CUSTOM, "tts_voice": "deep-man"}, {"id": "farmer"}])
    speech = year_speech(result("runoff", "custom-river", "custom-river-1"), BEFORE,
                         after(BEFORE), advisors=roster)
    assert speech["speaker"] == "custom-river" and speech["voice"] == "deep-man"
    assert speech["text"].startswith("My plan won the runoff.")


def test_safe_fallback_has_no_winner_so_the_narrator_states_it():
    speech = year_speech(result("escalated", "fallback", "safe"), BEFORE, after(BEFORE))
    assert speech["speaker"] == speech["voice"] == "narrator"
    assert "safe plan" in speech["text"]


# ---- the chamber ----

async def test_each_year_records_the_winner_speech_and_its_audio(tmp_path):
    journal = Store(tmp_path / "run.sqlite3")
    heard = []

    async def narrate(year, speech):
        heard.append(speech)
        return {"speech": f"/s/{year}", "headline": f"/h/{year}"}

    published = []

    async def emit(kind, data):
        if kind == "year":
            published.append(data)

    await play_council(1, unanimous, store=journal, max_rounds=1, narrate=narrate, emit=emit)
    years = [e["data"] for e in journal.events(published[0]["_run_id"]) if e["kind"] == "year"]
    journal.db.close()
    assert heard and len(published) == len(heard) == len(years)
    for spoken, live, stored in zip(heard, published, years, strict=True):
        assert stored["speech"] == spoken == live["speech"]
        assert spoken["voice"] in voice.SEAT_VOICES | {"narrator"}
        assert live["speech_url"] == f"/s/{live['year']}"
        assert live["headline_url"] == f"/h/{live['year']}"


async def test_narrate_uses_the_winner_voice_then_the_narrator(monkeypatch):
    from sumer import web

    monkeypatch.setattr(web, "live", web.Live())
    calls = []

    async def speak(key, text):
        calls.append(("speak", key, text))
        return b"x" * voice.BYTES_PER_SECOND

    async def narrate(text):
        calls.append(("narrate", text))
        return b"y" * voice.BYTES_PER_SECOND

    monkeypatch.setattr(voice, "speak", speak)
    monkeypatch.setattr(voice, "narrate", narrate)
    urls = await web._narrate(3, {"speaker": "merchant", "voice": "merchant",
                                  "text": "We will buy land.", "headline": "We bought 9 acres of land."})
    assert calls == [("speak", "merchant", "We will buy land."), ("narrate", "We bought 9 acres of land.")]
    assert urls["speech"].startswith("/api/narration/3?part=speech")
    assert urls["headline"].startswith("/api/narration/3?part=headline")
    assert set(web.live.narration) == {(3, "speech"), (3, "headline")}


async def test_without_a_key_the_speech_is_text_only():
    from sumer import web

    web.live.narration.clear()
    urls = await web._narrate(1, {"speaker": "farmer", "voice": "farmer",
                                  "text": "We will sow.", "headline": "Rats ate 5 bushels."})
    assert urls == {} and web.live.narration == {}


def test_voice_route_accepts_only_listed_keys():
    from sumer import web

    with TestClient(web.app) as client:
        meta = client.get("/api/meta").json()
        assert [v["id"] for v in meta["voices"]] == list(voice.CUSTOM_VOICES)
        assert all(a["tts_voice"] == a["id"] for a in meta["advisors"])
        assert client.get("/api/voice/21m00Tcm4TlvDq8ikWAM", params={"text": "hi"}).status_code == 404
        assert client.get("/api/voice/narrator", params={"text": "hi"}).status_code == 404
        # A listed key with no key or cache is simply unavailable; the text still shows.
        assert client.get("/api/voice/young-man", params={"text": "hi"}).status_code == 404
        assert client.get("/api/voice/young-man", params={"text": "x" * 401}).status_code == 422
        assert client.get("/api/narration/1", params={"part": "other"}).status_code == 422


def test_game_rejects_a_raw_voice_id_for_a_custom_seat(monkeypatch):
    from sumer import web

    journal = Store(":memory:")
    monkeypatch.setattr(web, "store", journal)
    with TestClient(web.app) as client:
        bad = client.post("/api/game", json={"advisors": [{**CUSTOM, "tts_voice": "ZQe5CZNOzWyzPSCn5a3c"}]})
        assert bad.status_code == 422
    journal.db.close()
