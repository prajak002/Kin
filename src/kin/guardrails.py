"""Deterministic guardrails around the model.

Safety-critical behaviour shouldn't depend only on the model following its
prompt, so two checks run on every turn:

- emergency_in(text): the person mentions a fall, chest pain, breathing trouble
  and so on. If the model didn't raise an urgent alert itself, run_turn raises one.
- dosing_advice_in(reply): the reply tells the person to take, double, skip or
  change a dose. The reply is replaced with a safe one.
- scam_in(text): the person describes a common fraud aimed at older adults in
  India (OTP or PIN requests, KYC or "account blocked" calls, lottery prizes,
  "digital arrest", remote-access apps). If the model didn't warn the family,
  run_turn does.

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

# Latin-script cues use word boundaries; Hindi and Bengali ones don't, since
# their vowel signs aren't word characters to the regex engine.
SCAM = re.compile(
    r"\b(?:otp|one[- ]time (?:password|code)|cvv|(?:atm|upi|bank|card) pin|pin number"
    r"|kyc|account (?:will be |is |has been |got )?(?:blocked|suspended|frozen|closed)"
    r"|lottery|lucky draw|(?:won|win|winner of) (?:a |the )?(?:prize|lottery|car|jackpot)"
    r"|digital arrest|(?:cbi|customs|narcotics|cyber ?crime) (?:officer|department|call|case)"
    r"|parcel (?:with|containing|has) drugs|anydesk|teamviewer|quick ?support"
    r"|gift cards?|upi collect|scan (?:a|the|this) qr code to (?:get|receive)"
    r"|khata band|otp bata)\b"
    r"|ओटीपी|केवाईसी|लॉटरी|इनाम जीत|डिजिटल अरेस्ट|खाता (?:बंद|ब्लॉक)"
    r"|ওটিপি|কেওয়াইসি|লটারি|পুরস্কার জিত|ডিজিটাল অ্যারেস্ট|অ্যাকাউন্ট (?:বন্ধ|ব্লক)",
    re.IGNORECASE,
)

SAFE_DOSING_REPLY = (
    "I can't advise on doses. Your pharmacist or doctor can tell you what to do. "
    "Would you like me to let your family know?"
)


def scam_in(text: str) -> str | None:
    match = SCAM.search(text)
    return match.group(0) if match else None


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
