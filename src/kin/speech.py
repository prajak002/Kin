"""Spoken replies in Indian languages.

Browsers only speak a language if the device has a voice for it; without one,
Bengali or Hindi text goes to an English voice, which can read nothing but the
punctuation ("comma… question mark"). So Kin makes the audio itself with
Microsoft Edge's free read-aloud voices (edge-tts, no key) and the device plays it.
"""

from __future__ import annotations

import edge_tts

VOICES = {
    "bn": "bn-IN-TanishaaNeural",
    "hi": "hi-IN-SwaraNeural",
    "ur": "ur-IN-GulNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
    "mr": "mr-IN-AarohiNeural",
    "en": "en-IN-NeerjaNeural",
}
MAX_CHARS = 1500  # a few spoken sentences; Kin's replies are much shorter


async def synthesise(text: str, lang: str) -> bytes:
    """MP3 audio of text in the given language (a code from VOICES)."""
    voice = VOICES.get(lang)
    if voice is None:
        raise ValueError(f"No voice for '{lang}'.")
    audio = bytearray()
    # A touch slower than the default: easier to follow for older listeners.
    async for chunk in edge_tts.Communicate(text[:MAX_CHARS], voice, rate="-8%").stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    if not audio:
        raise RuntimeError("The voice service returned no audio.")
    return bytes(audio)
