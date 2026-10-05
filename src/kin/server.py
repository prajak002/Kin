"""Kin's backend as one ASGI app, for Vercel (or any host):

  /mcp          MCP server (Streamable HTTP, stateless): Alexa+ and the dashboard
  /invocations  the agent, AgentCore-compatible: {"prompt", "person_id"}
  /traces       recent turn traces (latency, tools, guardrails) for the dashboard
  /telegram     Telegram bot webhook: family link up and ask questions
  /whatsapp     WhatsApp Cloud API webhook: family ask questions and send voice notes
  /voice-notes/<id>  a family voice note, for the person's device
  /speech       a reply as MP3 in an Indian language: {"text", "lang"}
  /cron/daily   evening digest and missed check-in alerts (Vercel Cron)
  /cron/reminders  medication reminders to family, run every few minutes
  /health       liveness

Run locally with `uv run uvicorn kin.server:app --port 8000`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from strands import Agent

from . import family_agent, guardrails, mcp_server
from .agent import build_agent, inprocess_tools
from . import conditions
from . import inbox, reminders, speech
from .format import status_text
from .notify import notify_family, send_whatsapp_text
from .turns import run_turn

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
log = logging.getLogger("kin.server")

server = mcp_server.server
_tools: list | None = None
_agents: dict[str, Agent] = {}  # used when the store can't hold chat history


@server.custom_route("/health", methods=["GET"])
async def health(_: Request) -> Response:
    return JSONResponse({"ok": True})


@server.custom_route("/invocations", methods=["POST"])
async def invocations(request: Request) -> Response:
    global _tools
    payload = await request.json()
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "payload needs a 'prompt'"}, status_code=400)
    person_id = payload.get("person_id") or "default"
    key = f"{request.headers.get(SESSION_HEADER) or 'default'}:{person_id}"

    store = mcp_server.store
    _tools = _tools or await inprocess_tools()
    if hasattr(store, "load_chat"):
        # Serverless: any instance may get the next turn, so history lives in the store.
        agent = build_agent(person_id, tools=_tools, quiet=True, messages=store.load_chat(key))
    else:
        agent = _agents.get(key) or _agents.setdefault(key, build_agent(person_id, tools=_tools, quiet=True))
    try:
        turn = await run_turn(agent, prompt, person_id=person_id, channel=payload.get("channel", "voice"), raise_alert=_alert)
    except Exception:
        # The model call failed (rate limit, a malformed tool call). The turn is traced as an
        # error; the person hears an apology instead of silence. Emergencies still escalate.
        log.exception("agent turn failed")
        if guardrails.emergency_in(prompt):
            _alert(person_id, f"Possible emergency: the person said “{guardrails.emergency_in(prompt)}”. Please check on them now.")
            return JSONResponse({"reply": "I didn't quite manage that, but I've let your family know right away. If you can, call emergency services."})
        return JSONResponse({"reply": "Sorry, I missed that. Could you say it again?"})
    if hasattr(store, "save_chat"):
        store.save_chat(key, agent.messages)
    return JSONResponse({"reply": turn.reply, "trace": turn.trace, "actions": turn.actions})


def _alert(person_id: str, reason: str, level: str = "urgent") -> None:
    """Fallback used by the guardrails when the model didn't alert."""
    if person := mcp_server.store.get_person(person_id):
        mcp_server.raise_alert(person, level, reason)


@server.custom_route("/traces", methods=["GET"])
async def traces(request: Request) -> Response:
    limit = min(int(request.query_params.get("limit", 200)), 500)
    return JSONResponse({"traces": mcp_server.store.recent_traces(limit)})


async def _telegram_reply(chat_id: int, text: str) -> None:
    token = os.environ["KIN_TELEGRAM_BOT_TOKEN"]
    async with httpx.AsyncClient(timeout=8) as http:
        await http.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text})


@server.custom_route("/telegram", methods=["POST"])
async def telegram(request: Request) -> Response:
    secret = os.environ.get("KIN_TELEGRAM_WEBHOOK_SECRET")
    if not secret or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return Response(status_code=401)

    message = (await request.json()).get("message") or {}
    text, chat = (message.get("text") or "").strip(), message.get("chat") or {}
    if not text or "id" not in chat:
        return JSONResponse({"ok": True})
    chat_id = chat["id"]
    sender = (message.get("from") or {}).get("first_name") or "Family"
    store = mcp_server.store

    if text.startswith("/start"):
        person_id = text.removeprefix("/start").strip()
        if not person_id or not store.get_person(person_id):
            await _telegram_reply(chat_id, "Open the link from Kin's family page to connect to someone.")
        else:
            person = mcp_server.add_family_contact(person_id, sender, "telegram", str(chat_id))
            await _telegram_reply(
                chat_id,
                f"You're connected to {person['name']}. Kin will message you here if anything needs attention. "
                "Send any message to get an update.",
            )
        return JSONResponse({"ok": True})

    await _telegram_reply(chat_id, await family_agent.answer("telegram", str(chat_id), sender, text))
    return JSONResponse({"ok": True})


@server.custom_route("/cron/reminders", methods=["GET", "POST"])
async def reminders_job(request: Request) -> Response:
    """Every few minutes (an external scheduler, since Vercel's free cron runs daily):
    WhatsApp family when a dose is due or unconfirmed, even with Kin's device closed."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or request.headers.get("authorization") != f"Bearer {secret}":
        return Response(status_code=401)
    report = []
    for person in await asyncio.to_thread(mcp_server.store.list_people):
        if person.get("schedule"):
            result = await asyncio.to_thread(reminders.check, person, mcp_server.store,
                                             raise_alert=mcp_server.raise_alert, speak=False)
            report.append({"person": person["id"], "escalated": len(result["escalated"])})
    return JSONResponse({"ok": True, "people": report})


@server.custom_route("/cron/daily", methods=["GET"])
async def daily(request: Request) -> Response:
    """Evening run (Vercel Cron): flag anyone who hasn't checked in today and send
    each family one digest with mood, medication, alerts and weather advice."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or request.headers.get("authorization") != f"Bearer {secret}":
        return Response(status_code=401)

    store, report = mcp_server.store, []
    for person in store.list_people():
        reminders.check(person, store, raise_alert=mcp_server.raise_alert)  # tell family about unconfirmed doses
        summary = mcp_server.wellbeing_summary(person["id"], days=1)
        lines = [status_text(person, summary)]
        level = "info"
        if not store.today("checkins", person["id"]):
            store.add_alert(person["id"], "warning", f"No check-in from {person['name']} today.")
            lines.append("No check-in today. Maybe give them a call?")
            level = "warning"
        try:
            today = await conditions.local_conditions(person.get("lives_in") or person["hometown"])
            lines += [f"Weather tip: {a['say']}" for a in today["advice"][:2]]
        except Exception:
            pass  # the digest still goes out without weather
        sent = await asyncio.to_thread(notify_family, person, level, "\n".join(lines))
        report.append({"person": person["id"], "level": level, "sent": sum(r["ok"] for r in sent)})
    return JSONResponse({"ok": True, "people": report})


@server.custom_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp(request: Request) -> Response:
    """Meta WhatsApp Cloud API webhook: family members message Kin's number."""
    if request.method == "GET":  # Meta's one-time verification handshake
        q = request.query_params
        verify = os.environ.get("KIN_WHATSAPP_VERIFY_TOKEN")
        if verify and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == verify:
            return Response(q.get("hub.challenge", ""), media_type="text/plain")
        return Response(status_code=403)

    body = await request.body()
    if secret := os.environ.get("KIN_WHATSAPP_APP_SECRET"):
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
            return Response(status_code=401)

    for entry in json.loads(body or b"{}").get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                sender = msg["from"]
                if msg.get("type") == "audio":
                    reply = await asyncio.to_thread(_voice_note, sender, msg["audio"]["id"])
                elif msg.get("type") == "text":
                    reply = await family_agent.answer("whatsapp", sender, names.get(sender, ""), msg["text"]["body"])
                else:
                    continue
                try:
                    await asyncio.to_thread(send_whatsapp_text, sender, reply)
                except Exception:
                    # Still answer 200: Meta re-sends a message whose webhook fails,
                    # and the voice note would land in the inbox several times.
                    log.exception("WhatsApp reply failed")
    return JSONResponse({"ok": True})


def _voice_note(sender: str, media_id: str) -> str:
    """A family voice note: download, transcribe, and leave it in each linked person's inbox."""
    store = mcp_server.store
    people = family_agent.contacts_for(store, "whatsapp", sender)
    if not people:
        return "This chat isn't connected to anyone yet. Ask your family to add you on Kin's family page."
    try:
        audio, mime = inbox.download_whatsapp_media(media_id)
    except Exception:
        log.exception("voice note download failed")
        return "Sorry, I couldn't fetch that voice note. Could you send it again?"
    for person in people:
        try:
            text = inbox.transcribe(audio, mime, person.get("language"))
        except Exception:
            log.exception("voice note transcription failed")
            text = ""
        inbox.leave_message(store, person, family_agent.sender_contact(person, "whatsapp", sender), text, audio, mime)
    names = " and ".join(p["name"] for p in people)
    return f"🎙️ Got it. Kin will play your voice note for {names} and send back anything they want to say."


@server.custom_route("/voice-notes/{message_id}", methods=["GET"])
async def voice_note(request: Request) -> Response:
    blob = await asyncio.to_thread(mcp_server.store.get_blob, f"voice:{request.path_params['message_id']}")
    if blob is None:
        return Response(status_code=404)
    data, mime = blob
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@server.custom_route("/speech", methods=["POST"])
async def speak(request: Request) -> Response:
    payload = await request.json()
    text, lang = (payload.get("text") or "").strip(), payload.get("lang") or ""
    if not text or lang not in speech.VOICES:
        return JSONResponse({"error": f"needs text and a lang from {sorted(speech.VOICES)}"}, status_code=400)
    try:
        audio = await speech.synthesise(text, lang)
    except Exception as e:
        log.exception("speech failed")
        return JSONResponse({"error": f"voice unavailable ({type(e).__name__})"}, status_code=502)
    return Response(audio, media_type="audio/mpeg")


class RequireToken:
    """When KIN_API_TOKEN is set, /mcp and /invocations need `Authorization: Bearer <token>`
    (or `?key=<token>` for MCP clients that can't set headers)."""

    PROTECTED = ("/mcp", "/invocations", "/traces", "/speech")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = os.environ.get("KIN_API_TOKEN")
        path = scope.get("path", "").rstrip("/")
        if token and scope["type"] == "http" and (path in self.PROTECTED or path.startswith("/voice-notes/")):
            request = Request(scope)
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("key")
            if not supplied or not hmac.compare_digest(supplied, token):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Stateless + JSON responses suit serverless; host 0.0.0.0 turns off the
# localhost-only DNS rebinding check, which would reject the public hostname.
def create_app():
    app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    app.add_middleware(RequireToken)
    return app


app = create_app()
