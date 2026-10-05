"""An agent for family members who message Kin on WhatsApp or Telegram.

"Did Mum take her tablets?" "How has Dad been this week?" The agent answers from
Kin's records with read-only tools. Which people a chat may ask about is decided
by the code (the chat must be a registered family contact), not by the prompt,
so a message can't talk the agent into reading someone else's data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from strands import Agent, ModelRetryStrategy, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from . import inbox, mcp_server
from .agent import build_model
from .format import status_text
from .turns import run_turn

PROMPT = """\
You are Kin's family assistant. Kin is a voice companion for older adults who
live alone; you answer their family's questions by text message. Today is {today}.

This chat belongs to {sender}, who is family of: {people}.

- Use the tools to look things up before answering; never guess.
- Reply like a text message: two or three short sentences, plain words, no
  markdown tables. Mention times as "this morning", "yesterday" and so on.
- Be warm and factual. If something looks worrying (low mood several days, missed
  doses, an urgent alert), say so plainly and suggest they call.
- Never give medical advice, diagnoses or dosing instructions.
- If they want to say something to the person ("tell Mum I'll visit Sunday"),
  call pass_message with their words; Kin's device reads it out to them. Tell
  them it will be passed on. They can also send a voice note.
- Otherwise you can only read information. If they ask you to change something,
  explain that it's done on Kin's family page.
- Only discuss the people listed above.
"""


def contacts_for(store, channel: str, address: str) -> list[dict[str, Any]]:
    """People whose family includes this chat."""
    digits = lambda a: "".join(ch for ch in a if ch.isdigit())  # noqa: E731
    match = digits(address) if channel == "whatsapp" else address
    return [
        p for p in store.list_people()
        if any(c["channel"] == channel and (digits(c["address"]) if channel == "whatsapp" else c["address"]) == match
               for c in p.get("family", []))
    ]


def sender_contact(person: dict[str, Any], channel: str, address: str) -> dict[str, Any]:
    digits = lambda a: "".join(ch for ch in a if ch.isdigit())  # noqa: E731
    same = (lambda c: digits(c["address"]) == digits(address)) if channel == "whatsapp" else (lambda c: c["address"] == address)
    return next((c for c in person.get("family", []) if c["channel"] == channel and same(c)), {"name": "Family"})


def _scoped_tools(people: list[dict[str, Any]], channel: str = "", address: str = "") -> list:
    allowed = {p["id"]: p for p in people}

    def check(person_id: str) -> None:
        if person_id not in allowed:
            raise ValueError(f"Not allowed. This chat can only ask about: {', '.join(allowed)}")

    @tool
    def wellbeing(person_id: str, days: int = 7) -> dict:
        """Check-ins (mood 1-5 with notes), medication doses, alerts and reminiscence
        topics for one person over the last few days.

        Args:
            person_id: id of the person, one of the people this chat may ask about.
            days: how many days to look back (1-30).
        """
        check(person_id)
        return mcp_server.wellbeing_summary(person_id, max(1, min(days, 30)))

    @tool
    def profile(person_id: str) -> dict:
        """Name, age, hometown, medications and favourite films or music for one person.

        Args:
            person_id: id of the person, one of the people this chat may ask about.
        """
        check(person_id)
        p = allowed[person_id]
        return {k: p.get(k) for k in ("name", "birth_year", "hometown", "language", "medications", "favourites")}

    @tool
    def pass_message(person_id: str, message: str) -> dict:
        """Leave a message for the person; Kin's device reads it out to them.

        Args:
            person_id: id of the person, one of the people this chat may ask about.
            message: the family member's words, as they said them.
        """
        check(person_id)
        person = allowed[person_id]
        inbox.leave_message(mcp_server.store, person, sender_contact(person, channel, address), message.strip())
        return {"passed_on_to": person["name"]}

    return [wellbeing, profile, pass_message]


async def answer(channel: str, address: str, sender: str, text: str) -> str:
    """Reply to a family member's message. Unknown chats get instructions."""
    store = mcp_server.store
    people = contacts_for(store, channel, address)
    if not people:
        return "This chat isn't connected to anyone yet. Ask your family to add you on Kin's family page."

    session = f"family:{channel}:{address}"
    agent = Agent(
        model=build_model(),
        tools=_scoped_tools(people, channel, address),
        system_prompt=PROMPT.format(
            today=date.today().isoformat(),
            sender=sender or "a family member",
            people=", ".join(f"{p['name']} (id {p['id']})" for p in people),
        ),
        messages=store.load_chat(session) if hasattr(store, "load_chat") else None,
        conversation_manager=SlidingWindowConversationManager(window_size=12),
        retry_strategy=ModelRetryStrategy(max_attempts=2, initial_delay=1, max_delay=2),
        callback_handler=None,
    )
    try:
        turn = await run_turn(agent, text, person_id=people[0]["id"], channel=f"{channel}-family")
    except Exception:
        # The model is unavailable: a plain summary is better than silence.
        return "\n\n".join(status_text(p, mcp_server.wellbeing_summary(p["id"], days=2)) for p in people)
    if hasattr(store, "save_chat"):
        store.save_chat(session, agent.messages)
    return turn.reply
