"""One conversational turn with tracing and guardrails.

Every path that talks to the agent (the HTTP backend, family chat agents, the
eval harness) goes through run_turn, so they all get the same trace:

    {at, channel, person, latency_ms, model, tools: [{name, ok}], tokens,
     guardrails: [...], error}

Traces never contain what was said, only sizes and outcomes, so they can go to
logs and the system dashboard without exposing personal conversations.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from strands import Agent

from . import guardrails
from .agent import MODEL_ID, PROVIDER

log = logging.getLogger("kin.turns")


@dataclass
class Turn:
    reply: str
    trace: dict[str, Any] = field(default_factory=dict)


def _tool_calls(messages: list[dict]) -> list[dict[str, Any]]:
    """Tool calls in the given messages, in order, with whether each succeeded."""
    results = {
        b["toolResult"]["toolUseId"]: b["toolResult"].get("status") == "success"
        for m in messages for b in m.get("content", []) if "toolResult" in b
    }
    return [
        {"name": b["toolUse"]["name"], "input": b["toolUse"].get("input", {}), "ok": results.get(b["toolUse"]["toolUseId"], False)}
        for m in messages for b in m.get("content", []) if "toolUse" in b
    ]


def _tokens(agent: Agent) -> dict[str, int]:
    invocations = agent.event_loop_metrics.agent_invocations
    usage = invocations[-1].usage if invocations else {}
    return {"input": usage.get("inputTokens", 0), "output": usage.get("outputTokens", 0)}


async def run_turn(
    agent: Agent,
    text: str,
    *,
    person_id: str,
    channel: str = "voice",
    raise_alert=None,
) -> Turn:
    """Run one turn. raise_alert(person_id, reason) is the emergency fallback."""
    started = time.perf_counter()
    before = len(agent.messages)
    trace: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel,
        "person": person_id,
        "model": f"{PROVIDER}:{MODEL_ID}",
        "input_chars": len(text),
        "guardrails": [],
    }
    try:
        reply = str(await agent.invoke_async(text)).strip()
    except Exception as e:
        trace.update(error=type(e).__name__, latency_ms=round((time.perf_counter() - started) * 1000))
        _record(trace)
        raise

    calls = _tool_calls(agent.messages[before:])
    trace["tools"] = [{"name": c["name"], "ok": c["ok"]} for c in calls]
    trace["tokens"] = _tokens(agent)

    if (advice := guardrails.dosing_advice_in(reply)) is not None:
        trace["guardrails"].append({"rule": "dosing_advice", "action": "replaced_reply", "matched": advice})
        reply = guardrails.SAFE_DOSING_REPLY

    if (emergency := guardrails.emergency_in(text)) is not None:
        alerted = any(c["name"] == "alert_family" and c["ok"] and c["input"].get("level") == "urgent" for c in calls)
        if not alerted and raise_alert is not None:
            raise_alert(person_id, f"Possible emergency: the person said “{emergency}”. Please check on them now.")
            trace["guardrails"].append({"rule": "emergency", "action": "raised_urgent_alert", "matched": emergency})
            reply = f"{reply} I've also let your family know right away.".strip()
        else:
            trace["guardrails"].append({"rule": "emergency", "action": "model_already_alerted", "matched": emergency})

    trace["latency_ms"] = round((time.perf_counter() - started) * 1000)
    trace["reply_chars"] = len(reply)
    _record(trace)
    return Turn(reply, trace)


def _record(trace: dict[str, Any]) -> None:
    # One JSON line per turn: searchable in Vercel / CloudWatch logs.
    log.info(json.dumps({"event": "kin.turn", **trace}))
    from . import mcp_server

    try:
        mcp_server.store.log_trace(trace)
    except Exception:  # observability must never break a conversation
        log.exception("could not store trace")
