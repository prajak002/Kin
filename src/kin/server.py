"""Kin's backend as one ASGI app, for Vercel (or any host):

  /mcp          MCP server (Streamable HTTP, stateless): Alexa+ and the dashboard
  /invocations  the agent, AgentCore-compatible: {"prompt", "person_id"}
  /telegram     Telegram bot webhook: family link up and ask for updates
  /health       liveness

Run locally with `uv run uvicorn kin.server:app --port 8000`.
"""

from __future__ import annotations

import hmac
import os

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from strands import Agent

from . import mcp_server
from .agent import build_agent, inprocess_tools
from .format import status_text

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"

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
        result = await agent.invoke_async(prompt)
        store.save_chat(key, agent.messages)
    else:
        agent = _agents.get(key) or _agents.setdefault(key, build_agent(person_id, tools=_tools, quiet=True))
        result = await agent.invoke_async(prompt)
    return JSONResponse({"reply": str(result).strip()})


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

    people = [p for p in store.list_people()
              if any(c["channel"] == "telegram" and c["address"] == str(chat_id) for c in p.get("family", []))]
    if not people:
        await _telegram_reply(chat_id, "This chat isn't connected to anyone yet. Use the link on Kin's family page.")
    else:
        updates = [status_text(p, mcp_server.wellbeing_summary(p["id"], days=2)) for p in people]
        await _telegram_reply(chat_id, "\n\n".join(updates))
    return JSONResponse({"ok": True})


class RequireToken:
    """When KIN_API_TOKEN is set, /mcp and /invocations need `Authorization: Bearer <token>`
    (or `?key=<token>` for MCP clients that can't set headers)."""

    PROTECTED = ("/mcp", "/invocations")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = os.environ.get("KIN_API_TOKEN")
        if token and scope["type"] == "http" and scope["path"].rstrip("/") in self.PROTECTED:
            request = Request(scope)
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("key")
            if not supplied or not hmac.compare_digest(supplied, token):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Stateless + JSON responses suit serverless; host 0.0.0.0 turns off the
# localhost-only DNS rebinding check, which would reject the public hostname.
app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
app.add_middleware(RequireToken)
