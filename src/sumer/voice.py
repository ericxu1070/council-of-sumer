"""Optional ElevenLabs text-to-speech, one voice per advisor, cached on disk.

Cached audio replays offline, so rehearse the demo once with Wi-Fi on.
"""

from __future__ import annotations

import hashlib
import os

import httpx

from sumer.config import CACHE_DIR, settings

# Premade ElevenLabs voices; override with ELEVENLABS_VOICE_<ADVISOR>.
DEFAULT_VOICES = {
    "farmer": "TxGEqnHWrfWFTfGW9XjX",  # Josh
    "priest": "VR6AewLTigWG4xSOukaG",  # Arnold
    "general": "pNInz6obpgDQGcFmaJgB",  # Adam
    "merchant": "ErXwobaYiN019PkySvjV",  # Antoni
}


def enabled() -> bool:
    return bool(settings.elevenlabs_api_key)


def voice_id(advisor: str) -> str:
    return os.getenv(f"ELEVENLABS_VOICE_{advisor.upper()}", DEFAULT_VOICES[advisor])


async def speak(advisor: str, text: str) -> bytes | None:
    key = hashlib.sha256(f"{voice_id(advisor)}:{text}".encode()).hexdigest()
    path = CACHE_DIR / "voice" / f"{key}.mp3"
    if path.exists():
        return path.read_bytes()
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id(advisor)}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json={"text": text, "model_id": "eleven_flash_v2_5"},
            )
            r.raise_for_status()
    except httpx.HTTPError:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content
