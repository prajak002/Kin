"""Deterministic guardrails around the model.

Safety-critical behaviour shouldn't depend only on the model following its
prompt, so two checks run on every turn:

- emergency_in(text): the person mentions a fall, chest pain, breathing trouble
  and so on. If the model didn't raise an urgent alert itself, run_turn raises one.
- dosing_advice_in(reply): the reply tells the person to take, double, skip or
  change a dose. The reply is replaced with a safe one.

Both are pattern-based on purpose: fast, explainable, and testable offline.
"""

from __future__ import annotations

import re

EMERGENCY = re.compile(
    r"\b(fell|fallen|fall(?:en)? down|i fall|slipped|tripped|can'?t get up|"
    r"chest pain|pain in my chest|heart attack|can'?t breathe|cannot breathe|short(?:ness)? of breath|"
    r"stroke|can'?t move my|numb(?:ness)?|bleeding (?:a lot|heavily)|unconscious|fainted|"
    r"help me|emergency|call an ambulance)\b",
    re.IGNORECASE,
)

# Instructions about doses: "take another tablet", "double your dose", "skip it",
# "stop taking", "take 500 mg", "increase your dose".
DOSING = re.compile(
    r"\b(?:"
    r"take (?:it|them|one|another|an extra|two|a double|(?:your|the) (?:next )?(?:dose|tablet|pill|medicine|medication)s?) (?:now|right away|immediately|later|tonight)"
    r"|take (?:another|an extra|two|a double) (?:dose|tablet|pill)"
    r"|double (?:up|your dose|the dose)"
    r"|skip (?:it|the|your|this) ?(?:dose|tablet|pill|medicine|medication)?"
    r"|stop taking"
    r"|(?:increase|decrease|reduce|raise|lower) (?:your|the) (?:dose|dosage)"
    r"|take \d+\s?(?:mg|milligrams?|ml|tablets?|pills?)"
    r")\b",
    re.IGNORECASE,
)

SAFE_DOSING_REPLY = (
    "I can't advise on doses. Your pharmacist or doctor can tell you what to do. "
    "Would you like me to let your family know?"
)


def emergency_in(text: str) -> str | None:
    match = EMERGENCY.search(text)
    return match.group(0) if match else None


# "Please don't double the dose" is the safe answer, not advice to do it.
NEGATED = re.compile(r"\b(?:don'?t|do not|never|shouldn'?t|should not|must not|not to|avoid|no need to)\W+(?:\w+\W+){0,2}$", re.IGNORECASE)


def dosing_advice_in(reply: str) -> str | None:
    for match in DOSING.finditer(reply):
        if not NEGATED.search(reply[max(0, match.start() - 40):match.start()]):
            return match.group(0)
    return None
