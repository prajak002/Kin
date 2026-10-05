"""Messages between family and the person Kin looks after.

Family to person: a WhatsApp voice note (or a text the family agent passes on,
"tell Mum I'll visit Sunday") lands in the person's inbox. The voice note is
downloaded from Meta, transcribed with Whisper, and kept for a week. Kin's
device collects new messages, plays the voice, and Kin mentions it warmly.

Person to family: "tell Ravi I'm fine" becomes reply_to_family, which sends the
words to that family member on WhatsApp or Telegram.

Rows in the "inbox" table: {id, person_id, from, relation, text, audio, at}.
The person's profile keeps inbox_seen_at, so each message is delivered once.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx

MAX_AUDIO = 700_000  # bytes: about a minute of WhatsApp voice; longer notes keep only the words
LANGUAGE_CODES = {"bengali": "bn", "bangla": "bn", "hindi": "hi", "english": "en"}


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{os.environ.get('KIN_WHATSAPP_API_VERSION', 'v23.0')}/{path}"


def download_whatsapp_media(media_id: str) -> tuple[bytes, str]:
    """Meta hands out a short-lived URL for each media id; both calls need the token."""
    headers = {"Authorization": f"Bearer {os.environ['KIN_WHATSAPP_TOKEN']}"}
    meta = httpx.get(_graph(media_id), headers=headers, timeout=10).raise_for_status().json()
    audio = httpx.get(meta["url"], headers=headers, timeout=20).raise_for_status()
    return audio.content, meta.get("mime_type", "audio/ogg").split(";")[0]


def transcribe(audio: bytes, mime: str, language: str | None = None) -> str:
    """Whisper on the same OpenAI-compatible host as the agent (Groq's free tier).
    Family may speak English or Hindi to a Bengali-speaking parent, so the language
    is detected rather than assumed."""
    base = os.environ.get("KIN_OPENAI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac", "audio/amr": "amr"}.get(mime, "ogg")

    def whisper(code: str | None) -> dict[str, Any]:
        data = {"model": os.environ.get("KIN_STT_MODEL", "whisper-large-v3-turbo"), "response_format": "verbose_json"}
        if code:
            data["language"] = code
        r = httpx.post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['KIN_OPENAI_API_KEY']}"},
            data=data,
            files={"file": (f"note.{ext}", audio, mime)},
            timeout=30,
        )
        return r.raise_for_status().json()

    heard = whisper(None)
    detected = (heard.get("language") or "").lower()
    usual = LANGUAGE_CODES.get((language or "").lower())
    # Whisper reliably hears Bengali as Hindi (and Hindi as Urdu), even from a clear
    # voice. Family write in English or in the parent's language, so unless it heard
    # English, transcribe in the parent's language.
    if usual and detected != "english" and LANGUAGE_CODES.get(detected) != usual:
        again = whisper(usual)
        # Speech really in another language comes back empty or cut short: keep the first.
        if len(again["text"].strip()) >= 0.6 * len(heard["text"].strip()):
            heard = again
    return heard["text"].strip()


def leave_message(store, person: dict[str, Any], sender: dict[str, Any], text: str,
                  audio: bytes | None = None, mime: str = "audio/ogg") -> dict[str, Any]:
    """Put a message from a family member in the person's inbox."""
    message_id = secrets.token_hex(8)
    stored_audio = None
    if audio and len(audio) <= MAX_AUDIO:
        store.put_blob(f"voice:{message_id}", audio, mime)
        stored_audio = message_id
    return store.add_row("inbox", person["id"], id=message_id, sender=sender.get("name") or "Family",
                         relation=sender.get("relation", ""), text=text, audio=stored_audio)


def collect(store, person: dict[str, Any], deliver: bool = True) -> list[dict[str, Any]]:
    """Messages the person hasn't heard yet; with deliver, mark them heard."""
    seen = person.get("inbox_seen_at") or ""
    new = [m for m in store.recent("inbox", person["id"], 50) if m["at"] > seen]
    if new and deliver:
        store.upsert_person(person["id"], inbox_seen_at=max(m["at"] for m in new))
    return new


def contact_named(person: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The family contact a person means by "Ravi", "my son" or "the family"."""
    wanted = name.casefold().strip()
    words = set(wanted.replace(",", " ").split())
    reachable = [c for c in person.get("family", []) if c["channel"] in ("whatsapp", "telegram")]
    for c in reachable:
        relation = c.get("relation", "").casefold()
        if c["name"].casefold() in wanted or (relation and relation in words):
            return c
    # "Tell the family…" with one reachable contact means them.
    if words & {"family", "everyone", "them", "kids", "children"} and len(reachable) == 1:
        return reachable[0]
    return None
