"""Clamoring: short crowd shouts ("Sell the land!") voiced by each advisor.

The browser derives each advisor's stance from the plan it currently backs and
asks for one of a few fixed phrases per stance. Only whitelisted phrases are
ever sent to ElevenLabs, so the set of clips is small (stances x phrases x
voices) and every clip is cached on disk after its first use.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from sumer import voice

# Stance -> reusable 2-4 word shouts. Keep this list short: each entry costs one
# TTS call per voice, once, and is then served from the disk cache.
PHRASES: dict[str, tuple[str, ...]] = {
    "buy_land": ("Buy more land!", "More fields!"),
    "sell_land": ("Sell the land!", "Sell it now!"),
    "keep_land": ("Keep our land!",),
    "feed": ("Feed the people!", "Fill the bowls!"),
    "ration": ("Cut the rations!", "Tighten our belts!"),
    "starve": ("Let them starve!",),
    "plant": ("Sow every field!", "Plant the seed!"),
    "fallow": ("Leave fields fallow!",),
    "store": ("Store the grain!", "Fill the granary!"),
}

# Shouts are punchier and less steady than speeches.
SHOUT_SETTINGS = {"stability": 0.3, "similarity_boost": 0.75, "style": 0.6, "use_speaker_boost": True}

router = APIRouter()


def phrase(stance: str, index: int) -> str:
    """The whitelisted shout for a stance; the index wraps so any int is valid."""
    options = PHRASES.get(stance)
    if not options:
        raise KeyError(stance)
    return options[index % len(options)]


def voice_keys() -> set[str]:
    """Seat voices a shout may use: the built-in advisors and the custom-agent choices, never the narrator."""
    return set(voice.SEAT_VOICES)


async def shout(speaker: str, stance: str, index: int) -> bytes | None:
    return await voice._tts(speaker, phrase(stance, index), voice.ADVISOR_MODEL, SHOUT_SETTINGS)


@router.get("/api/clamor")
async def clamor_phrases():
    return JSONResponse({"voice": voice.enabled(), "voices": sorted(voice_keys()),
                         "phrases": {k: list(v) for k, v in PHRASES.items()}})


@router.get("/api/clamor/{speaker}/{stance}/{index}")
async def clamor_audio(speaker: str, stance: str, index: int):
    if speaker not in voice_keys() or stance not in PHRASES or not 0 <= index < len(PHRASES[stance]):
        raise HTTPException(404)
    audio = await shout(speaker, stance, index)
    if audio is None:
        raise HTTPException(404, "voice unavailable")
    # Clips never change for a given URL, so the browser can keep them.
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
