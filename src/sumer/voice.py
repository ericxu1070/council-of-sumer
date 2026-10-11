"""Optional ElevenLabs text-to-speech: one voice per advisor plus a narrator, cached on disk.

Cached audio replays offline, so rehearse the demo once with Wi-Fi on.
"""

from __future__ import annotations

import hashlib
import os

import httpx

from sumer.config import CACHE_DIR, settings

# Premade ElevenLabs voices; override with ELEVENLABS_VOICE_<ID> (e.g. ELEVENLABS_VOICE_BUILDER).
DEFAULT_VOICES = {
    "farmer": "TxGEqnHWrfWFTfGW9XjX",  # Josh
    "priest": "VR6AewLTigWG4xSOukaG",  # Arnold
    "general": "pNInz6obpgDQGcFmaJgB",  # Adam
    "merchant": "ErXwobaYiN019PkySvjV",  # Antoni
    "builder": "N2lVS1w4EtoT3dr4eOWO",  # Callum
    "healer": "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "diplomat": "onwK4e9ZLuTAKqWW03F9",  # Daniel
    "narrator": "nPczCjzI2devNBz1zQrb",  # Brian: deep, resonant storyteller
}
ADVISOR_MODEL = "eleven_flash_v2_5"  # fast, for short speeches
NARRATOR_MODEL = "eleven_multilingual_v2"  # richer delivery for the year summaries
OUTPUT_FORMAT = "mp3_44100_128"
BYTES_PER_SECOND = 128_000 // 8  # matches OUTPUT_FORMAT, used to time the narration


def enabled() -> bool:
    return bool(settings.elevenlabs_api_key)


def voice_id(speaker: str) -> str:
    return os.getenv(f"ELEVENLABS_VOICE_{speaker.upper()}", DEFAULT_VOICES[speaker])


async def _tts(speaker: str, text: str, model: str, voice_settings: dict | None = None):
    vid = voice_id(speaker)
    key = hashlib.sha256(f"{vid}:{model}:{text}".encode()).hexdigest()
    path = CACHE_DIR / "voice" / f"{key}.mp3"
    if path.exists():
        return path.read_bytes()
    if not enabled():
        return None
    body: dict = {"text": text, "model_id": model}
    if voice_settings:
        body["voice_settings"] = voice_settings
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json=body,
            )
            r.raise_for_status()
    except httpx.HTTPError:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


async def speak(advisor: str, text: str) -> bytes | None:
    return await _tts(advisor, text, ADVISOR_MODEL)


async def narrate(text: str) -> bytes | None:
    """The year's summary in the narrator's deep, unhurried voice."""
    return await _tts(
        "narrator", text, NARRATOR_MODEL,
        {"stability": 0.6, "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": True},
    )


def seconds(audio: bytes | None, text: str) -> float:
    """How long the narration plays: from the mp3 size, or from the word count."""
    if audio:
        return len(audio) / BYTES_PER_SECOND
    return len(text.split()) / 2.6
