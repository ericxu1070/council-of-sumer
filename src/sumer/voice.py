"""Optional ElevenLabs text-to-speech: one voice per seat plus a narrator, cached on disk.

Cached audio replays offline, so rehearse the demo once with Wi-Fi on.
"""

from __future__ import annotations

import hashlib
import os

import httpx

from sumer.config import CACHE_DIR, settings

# Premade ElevenLabs voices, one distinct voice per built-in seat; override with
# ELEVENLABS_VOICE_<KEY> (e.g. ELEVENLABS_VOICE_PROVIDER).
DEFAULT_VOICES = {
    "provider": "XrExE9yKIg1WjnnlVkGX",  # Matilda: warm, caring woman
    "farmer": "pqHfZKP75CvOlQylNhV4",  # Bill: weathered, folksy older man
    "merchant": "cjVigY5qzO86Huf0OWal",  # Eric: smooth, persuasive man
    "priest": "onwK4e9ZLuTAKqWW03F9",  # Daniel: deep, measured, authoritative
    "villager": "cgSgspJ2msm6clMCkdW9",  # Jessica: plainspoken young woman
    "narrator": "nPczCjzI2devNBz1zQrb",  # Brian: deep, resonant storyteller
}

# Voices a custom agent may pick. The browser sends only these keys, never raw voice IDs.
CUSTOM_VOICES = {
    "elder-woman": ("Xb7hH8MSUJpSbSDYk0k2", "Alice - commanding older woman"),
    "young-woman": ("FGY2WhTYpPnrIDTdsKH5", "Laura - bright young woman"),
    "husky-woman": ("pFZP5JQG7iQjIQuC4Bku", "Lily - low, husky woman"),
    "elder-man": ("JBFqnCBsd6RMkjVDRZzb", "George - warm, raspy older man"),
    "young-man": ("TX3LPaxmHKxFdv7VOQHJ", "Liam - light, eager young man"),
    "deep-man": ("N2lVS1w4EtoT3dr4eOWO", "Callum - deep, gravelly man"),
}
CUSTOM_VOICE_KEYS = tuple(CUSTOM_VOICES)

# The herald who announces an omen: a dread voice for disasters, an excited one for good fortune.
HERALD_VOICES = {
    "herald_dread": "2EiwWnXFnvU5JabPnv8n",  # Clyde: gravelly, ominous
    "herald_joy": "IKne3meq5aSn9XLyUdCD",  # Charlie: bright, energetic
}
# Fixed lines only, so every announcement is cached once and the client never sends text.
OMEN_LINES = {
    "plague": ("herald_dread", "Hear me... a sickness creeps through the reed huts. The plague... has come to Sumer."),
    "rats": ("herald_dread", "Listen... in the dark of the granary, something gnaws. The rats... are in the grain."),
    "bad_harvest": ("herald_dread", "The gods have turned their faces away... the fields wither. This harvest... will fail."),
    "good_harvest": ("herald_joy", "Rejoice! The gods smile on Sumer! The fields are bursting, a great harvest is coming!"),
    "low_price": ("herald_joy", "Wonderful news! Land is cheap! Buy it up, every field is going for a song!"),
    "high_price": ("herald_joy", "Ha! Land is worth a fortune now! Every acre we hold is gold!"),
}
HERALD_SETTINGS = {
    "herald_dread": {"stability": 0.35, "similarity_boost": 0.8, "style": 0.6, "use_speaker_boost": True},
    "herald_joy": {"stability": 0.3, "similarity_boost": 0.75, "style": 0.8, "use_speaker_boost": True},
}

# Keys a council seat may speak in: a built-in advisor's voice or a custom voice.
SEAT_VOICES = frozenset([*(k for k in DEFAULT_VOICES if k != "narrator"), *CUSTOM_VOICES])

ADVISOR_MODEL = "eleven_flash_v2_5"  # fast, for short speeches
NARRATOR_MODEL = "eleven_multilingual_v2"  # richer delivery for the narrator's headlines
OUTPUT_FORMAT = "mp3_44100_128"
BYTES_PER_SECOND = 128_000 // 8  # matches OUTPUT_FORMAT, used to time the year-end speech


def enabled() -> bool:
    return bool(settings.elevenlabs_api_key)


def custom_voice_for(seat_id: str) -> str:
    """A stable custom voice for a seat that did not pick one."""
    return CUSTOM_VOICE_KEYS[sum(map(ord, seat_id)) % len(CUSTOM_VOICE_KEYS)]


def custom_voice_options() -> list[dict]:
    return [{"id": k, "label": label} for k, (_, label) in CUSTOM_VOICES.items()]


def voice_id(speaker: str) -> str | None:
    """The ElevenLabs voice for a known key; None for anything else."""
    if speaker in DEFAULT_VOICES:
        default = DEFAULT_VOICES[speaker]
    elif speaker in CUSTOM_VOICES:
        default = CUSTOM_VOICES[speaker][0]
    elif speaker in HERALD_VOICES:
        default = HERALD_VOICES[speaker]
    else:
        return None
    return os.getenv(f"ELEVENLABS_VOICE_{speaker.upper().replace('-', '_')}", default)


async def _tts(speaker: str, text: str, model: str, voice_settings: dict | None = None):
    vid = voice_id(speaker)
    if vid is None or not text.strip():
        return None
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


async def speak(voice: str, text: str) -> bytes | None:
    """A seat speaking in its voice key (one of SEAT_VOICES); None if unknown or unavailable."""
    if voice not in SEAT_VOICES:
        return None
    return await _tts(voice, text, ADVISOR_MODEL)


async def narrate(text: str) -> bytes | None:
    """The narrator's deep, unhurried voice, for the year's headline."""
    return await _tts(
        "narrator", text, NARRATOR_MODEL,
        {"stability": 0.6, "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": True},
    )


async def announce(omen: str) -> bytes | None:
    """The herald's fixed line for an omen: ominous for disasters, excited for good fortune."""
    if omen not in OMEN_LINES:
        return None
    herald, text = OMEN_LINES[omen]
    return await _tts(herald, text, NARRATOR_MODEL, HERALD_SETTINGS[herald])


def seconds(audio: bytes | None, text: str) -> float:
    """How long a clip plays: from the mp3 size, or from the word count."""
    if audio:
        return len(audio) / BYTES_PER_SECOND
    return len(text.split()) / 2.6
