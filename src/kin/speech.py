"""Spoken replies in Indian languages.

Browsers only speak a language if the device has a voice for it; without one,
Bengali or Hindi text goes to an English voice, which can read nothing but the
punctuation ("comma… question mark"). So Kin makes the audio itself with
Microsoft Edge's free read-aloud voices (edge-tts, no key) and the device plays it.
Edge sometimes refuses requests from cloud hosts, so each reply gets a few tries
and then Google Translate's read-aloud voice as a backup (also free and keyless).
"""

from __future__ import annotations

import asyncio
import logging
import re

import edge_tts
import httpx

log = logging.getLogger("kin.speech")

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
    text = text[:MAX_CHARS]
    for attempt in range(3):
        try:
            return await _edge(text, voice)
        except Exception as e:  # NoAudioReceived, handshake refusals, timeouts
            log.warning("edge-tts attempt %d failed: %s", attempt + 1, type(e).__name__)
            await asyncio.sleep(0.4 * (attempt + 1))
    return await _google(text, lang)


async def _edge(text: str, voice: str) -> bytes:
    audio = bytearray()
    # A touch slower than the default: easier to follow for older listeners.
    async for chunk in edge_tts.Communicate(text, voice, rate="-8%", receive_timeout=8).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    if not audio:
        raise RuntimeError("no audio")
    return bytes(audio)


def _chunks(text: str, size: int = 180) -> list[str]:
    """Google's voice takes about 200 characters per request: split at sentence
    ends, then commas, then spaces."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?।])\s+|(?<=,)\s+", text):
        while len(part) > size:
            cut = part.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            out.append(part[:cut])
            part = part[cut:].lstrip()
        if out and len(out[-1]) + len(part) + 1 <= size:
            out[-1] = f"{out[-1]} {part}"
        elif part:
            out.append(part)
    return out


async def _google(text: str, lang: str) -> bytes:
    audio = bytearray()
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as http:
        for piece in _chunks(text):
            r = await http.get("https://translate.google.com/translate_tts",
                               params={"ie": "UTF-8", "tl": lang, "client": "tw-ob", "q": piece})
            audio += r.raise_for_status().content  # MP3 frames join cleanly
    if not audio:
        raise RuntimeError("The voice services returned no audio.")
    return bytes(audio)
