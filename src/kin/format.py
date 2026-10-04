"""Plain-text updates for family messages."""

from __future__ import annotations

from datetime import datetime

MOOD_WORDS = {1: "very low", 2: "low", 3: "okay", 4: "good", 5: "very good"}


def _when(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%a %H:%M")


def status_text(person: dict, summary: dict) -> str:
    """A short update on one person from a wellbeing summary."""
    lines = [person["name"]]
    if summary["checkins"]:
        last = summary["checkins"][-1]
        note = f" ({last['notes']})" if last.get("notes") else ""
        lines.append(f"Mood: {MOOD_WORDS.get(last['mood'], last['mood'])}{note}, {_when(last['at'])}")
    else:
        lines.append("No check-in in the last 2 days.")
    if summary["missed_doses"]:
        missed = ", ".join(sorted({d["medication"] for d in summary["missed_doses"]}))
        lines.append(f"Missed medication: {missed}")
    flagged = [a for a in summary["alerts"] if a["level"] != "info"]
    if flagged:
        lines.append(f"Flagged: {flagged[-1]['reason']} ({_when(flagged[-1]['at'])})")
    if summary["moments"]:
        lines.append(f"Chatted about: {', '.join(summary['moments'][-1]['items'][:3])}")
    return "\n".join(lines)
