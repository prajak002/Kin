"""Talk to Kin out loud: microphone -> Whisper -> agent -> speech.

Speech to text uses Whisper on the OpenAI-compatible host in KIN_OPENAI_BASE_URL
(Groq's free tier serves whisper-large-v3-turbo). Replies are spoken with
Orpheus on the same host when KIN_TTS=orpheus, otherwise with macOS `say`;
`say` is also the fallback if Orpheus fails.

    uv run kin-voice asha              # conversation through the mic
    uv run kin-voice asha --file a.wav # one turn from a recording
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import tempfile
import wave

import httpx
from dotenv import load_dotenv

load_dotenv()

RATE = 16_000
BLOCK = 1_600  # 100 ms
STT_MODEL = os.environ.get("KIN_STT_MODEL", "whisper-large-v3-turbo")
TTS_MODEL = os.environ.get("KIN_TTS_MODEL", "canopylabs/orpheus-v1-english")
GOODBYES = {"goodbye", "bye", "good night", "stop"}


def _api() -> tuple[str, dict[str, str]]:
    base = os.environ.get("KIN_OPENAI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    return base, {"Authorization": f"Bearer {os.environ['KIN_OPENAI_API_KEY']}"}


def _wav(frames: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return buf.getvalue()


def record_utterance(silence_s: float = 1.2, max_s: float = 30.0) -> bytes:
    """Wait for speech, then record until the speaker pauses. Returns WAV bytes."""
    import numpy as np
    import sounddevice as sd

    blocks: list[bytes] = []
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:

        def level() -> tuple[bytes, float]:
            data, _ = stream.read(BLOCK)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            return bytes(data), float(np.sqrt(np.mean(samples**2)))

        floor = max(sum(level()[1] for _ in range(5)) / 5, 50.0)
        threshold = floor * 3
        # Keep a little audio from before speech starts so the first word isn't clipped.
        pre: list[bytes] = []
        while True:
            data, rms = level()
            pre = (pre + [data])[-3:]
            if rms > threshold:
                blocks = pre
                break
        quiet = 0
        while len(blocks) * BLOCK / RATE < max_s:
            data, rms = level()
            blocks.append(data)
            quiet = quiet + 1 if rms < threshold else 0
            if quiet * BLOCK / RATE >= silence_s:
                break
    return _wav(b"".join(blocks))


def transcribe(wav: bytes) -> str:
    base, headers = _api()
    r = httpx.post(
        f"{base}/audio/transcriptions",
        headers=headers,
        files={"file": ("speech.wav", wav, "audio/wav")},
        data={"model": STT_MODEL, "response_format": "text",
              **({"language": lang} if (lang := os.environ.get("KIN_STT_LANGUAGE")) else {})},
        timeout=60,
    )
    r.raise_for_status()
    return r.text.strip()


def _say(text: str) -> None:
    voice = os.environ.get("KIN_SAY_VOICE")
    subprocess.run(["say", *(["-v", voice] if voice else []), text], check=False)


def speak(text: str) -> None:
    if os.environ.get("KIN_TTS", "say") == "orpheus":
        base, headers = _api()
        try:
            r = httpx.post(
                f"{base}/audio/speech",
                headers=headers,
                json={"model": TTS_MODEL, "input": text, "voice": os.environ.get("KIN_TTS_VOICE", "hannah"),
                      "response_format": "wav"},
                timeout=60,
            )
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                f.write(r.content)
                f.flush()
                subprocess.run(["afplay", f.name], check=False)
            return
        except httpx.HTTPError as e:
            print(f"(Orpheus unavailable, using say: {e})")
    _say(text)


def turn(agent, wav: bytes) -> str | None:
    heard = transcribe(wav)
    print(f"you> {heard}")
    if not heard:
        return None
    reply = str(agent(heard)).strip()
    print(f"kin> {reply}")
    speak(reply)
    return heard


def main() -> None:
    from .agent import build_agent

    parser = argparse.ArgumentParser(description="Talk to Kin out loud.")
    parser.add_argument("person_id", nargs="?", default=os.environ.get("KIN_PERSON_ID", "default"))
    parser.add_argument("--file", help="answer one recorded utterance (WAV) instead of using the mic")
    args = parser.parse_args()

    agent = build_agent(args.person_id, quiet=True)
    if args.file:
        with open(args.file, "rb") as f:
            turn(agent, f.read())
        return

    print("Kin is listening. Say 'goodbye' or press Ctrl-C to stop.")
    try:
        while True:
            print("\n(listening...)")
            heard = turn(agent, record_utterance())
            if heard and heard.lower().strip(" .!") in GOODBYES:
                return
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
