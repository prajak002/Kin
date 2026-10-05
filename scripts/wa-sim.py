"""Chat with Kin's WhatsApp family agent as any number, without Meta.

Meta's test number only replies to numbers on its recipient list, so testing
several family members needs several phones. This sends correctly signed
webhook payloads through Kin's real /whatsapp route and prints the reply here
instead of sending it. It uses a throwaway store seeded with a demo person
(Asha) and two family members, so your real data is never touched.

  uv run python scripts/wa-sim.py                 # chat as Ravi (son)
  uv run python scripts/wa-sim.py --as meera      # chat as Meera (daughter)
  uv run python scripts/wa-sim.py --as +15550009999   # a number nobody added
  uv run python scripts/wa-sim.py --as ravi "Did Mum take her tablets?"

In the chat, `/as meera` switches sender and `/quit` leaves.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
# A throwaway local store: never Redis, DynamoDB or the shared vector memory.
for var in ("KV_REST_API_URL", "UPSTASH_REDIS_REST_URL", "KIN_TABLE", "UPSTASH_VECTOR_REST_URL"):
    os.environ.pop(var, None)
os.environ["KIN_STORE"] = str(Path(tempfile.mkdtemp(prefix="kin-wa-sim-")) / "store.json")
os.environ["KIN_API_TOKEN"] = ""  # /whatsapp is not token-protected, but keep the app simple
SECRET = os.environ.setdefault("KIN_WHATSAPP_APP_SECRET", "sim-secret")

FAMILY = {
    "ravi": {"name": "Ravi", "relation": "son", "channel": "whatsapp", "address": "+15550000001"},
    "meera": {"name": "Meera", "relation": "daughter", "channel": "whatsapp", "address": "+15550000002"},
}


def seed() -> None:
    from kin import mcp_server

    store = mcp_server.store
    store.upsert_person(
        "asha", name="Asha", birth_year=1948, hometown="Kolkata", language="en",
        medications=["Amlodipine 5 mg, morning", "Metformin 500 mg, morning and evening"],
        family=list(FAMILY.values()),
    )
    now = datetime.now(timezone.utc)
    ago = lambda **kw: (now - timedelta(**kw)).isoformat(timespec="seconds")  # noqa: E731
    store.add_checkin("asha", 4, "Talked about the garden; sounded cheerful.", at=ago(days=2))
    store.add_checkin("asha", 2, "Said she felt tired and a bit lonely.", at=ago(days=1))
    store.add_checkin("asha", 3, "Better today, watched an old Satyajit Ray film.", at=ago(hours=3))
    store.add_dose("asha", "Amlodipine", True, at=ago(hours=5))
    store.add_dose("asha", "Metformin", True, at=ago(hours=5))
    store.add_dose("asha", "Metformin", False, at=ago(hours=17))


def resolve(who: str) -> tuple[str, str]:
    if who.lower() in FAMILY:
        c = FAMILY[who.lower()]
        return c["address"].lstrip("+"), c["name"]
    return "".join(ch for ch in who if ch.isdigit()), ""


def send(client, sender: str, name: str, text: str, outbox: list) -> str:
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": sender, "profile": {"name": name}}],
        "messages": [{"from": sender, "id": f"wamid.sim{time.time_ns()}", "timestamp": str(int(time.time())),
                      "type": "text", "text": {"body": text}}],
    }}]}]}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    outbox.clear()
    r = client.post("/whatsapp", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    if r.status_code != 200:
        return f"[webhook returned {r.status_code}: {r.text}]"
    return "\n".join(t for _, t in outbox) or "[no reply sent]"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="who", default="ravi", help="ravi, meera, or any phone number")
    ap.add_argument("message", nargs="*", help="send one message and exit")
    args = ap.parse_args()

    seed()
    logging.disable(logging.WARNING)  # keep the chat readable
    from starlette.testclient import TestClient

    from kin import server

    outbox: list[tuple[str, str]] = []
    server.send_whatsapp_text = lambda phone, text: outbox.append((phone, text))  # print instead of calling Meta
    client = TestClient(server.app)

    sender, name = resolve(args.who)
    if args.message:
        print(send(client, sender, name, " ".join(args.message), outbox))
        return

    print(f"Simulated WhatsApp with Kin. Family: {', '.join(FAMILY)}. /as <name|number> switches, /quit leaves.")
    while True:
        try:
            text = input(f"\n{name or '+' + sender}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/quit", "/q"):
            break
        if text.startswith("/as "):
            sender, name = resolve(text[4:].strip())
            continue
        print(f"Kin: {send(client, sender, name, text, outbox)}")


if __name__ == "__main__":
    main()
