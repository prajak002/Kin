"""Family push notifications through ntfy (open source, self-hostable)."""

from __future__ import annotations

import os
import threading

import httpx

PRIORITY = {"info": "3", "warning": "4", "urgent": "5"}
TAGS = {"info": "information_source", "warning": "warning", "urgent": "rotating_light"}


def _post(url: str, title: str, body: str, level: str) -> None:
    try:
        httpx.post(
            url,
            content=body.encode(),
            headers={"Title": title, "Priority": PRIORITY.get(level, "3"), "Tags": TAGS.get(level, "")},
            timeout=5,
        )
    except httpx.HTTPError:
        pass


def notify_family(person_name: str, level: str, reason: str) -> bool:
    topic = os.environ.get("KIN_NTFY_TOPIC")
    if not topic:
        return False
    server = os.environ.get("KIN_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    title = f"Kin: {person_name}" if level != "urgent" else f"Urgent: {person_name} needs help"
    threading.Thread(target=_post, args=(f"{server}/{topic}", title, reason, level), daemon=True).start()
    return True
