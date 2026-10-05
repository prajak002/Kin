"""Family alerts over free channels: WhatsApp (Meta Cloud API), Telegram and ntfy.

Each person's profile lists family contacts as
    {"name": "Ravi", "relation": "son", "channel": "whatsapp" | "telegram" | "ntfy", "address": ...}
where address is a phone number in international format (WhatsApp), a chat id
(Telegram) or a topic (ntfy). KIN_NTFY_TOPIC, if set, also gets every alert.

Sends are synchronous with short timeouts: on serverless hosts, work left
running after the response can be cut off.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

CHANNELS = ("whatsapp", "telegram", "ntfy")
PRIORITY = {"info": "3", "warning": "4", "urgent": "5"}
TAGS = {"info": "information_source", "warning": "warning", "urgent": "rotating_light"}
TIMEOUT = 8
log = logging.getLogger("kin.notify")


def _title(person_name: str, level: str) -> str:
    return f"Urgent: {person_name} needs help" if level == "urgent" else f"Kin: {person_name}"


def send_ntfy(topic: str, person_name: str, level: str, reason: str) -> None:
    server = os.environ.get("KIN_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    httpx.post(
        f"{server}/{topic}",
        content=reason.encode(),
        headers={"Title": _title(person_name, level), "Priority": PRIORITY.get(level, "3"), "Tags": TAGS.get(level, "")},
        timeout=TIMEOUT,
    ).raise_for_status()


def send_telegram(chat_id: str, person_name: str, level: str, reason: str) -> None:
    token = os.environ["KIN_TELEGRAM_BOT_TOKEN"]
    icon = {"urgent": "🚨", "warning": "⚠️"}.get(level, "ℹ️")
    httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": f"{icon} {_title(person_name, level)}\n{reason}"},
        timeout=TIMEOUT,
    ).raise_for_status()


def send_whatsapp(phone: str, person_name: str, level: str, reason: str) -> None:
    """Meta WhatsApp Cloud API. Messages Kin starts need an approved template
    (KIN_WHATSAPP_TEMPLATE, with body parameters {{1}} = name and {{2}} = reason);
    without one, plain text only arrives if the contact messaged the number in the
    last 24 hours."""
    version = os.environ.get("KIN_WHATSAPP_API_VERSION", "v23.0")
    url = f"https://graph.facebook.com/{version}/{os.environ['KIN_WHATSAPP_PHONE_ID']}/messages"
    to = phone.lstrip("+").replace(" ", "")
    if template := os.environ.get("KIN_WHATSAPP_TEMPLATE"):
        body: dict[str, Any] = {
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": os.environ.get("KIN_WHATSAPP_TEMPLATE_LANG", "en")},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": person_name},
                    {"type": "text", "text": reason[:900]},
                ]}],
            },
        }
    else:
        body = {"type": "text", "text": {"body": f"{_title(person_name, level)}\n{reason}"}}
    httpx.post(
        url,
        headers={"Authorization": f"Bearer {os.environ['KIN_WHATSAPP_TOKEN']}"},
        json={"messaging_product": "whatsapp", "to": to, **body},
        timeout=TIMEOUT,
    ).raise_for_status()


def send_whatsapp_text(phone: str, text: str) -> None:
    """Free-form reply, allowed within 24 hours of the contact's last message."""
    version = os.environ.get("KIN_WHATSAPP_API_VERSION", "v23.0")
    httpx.post(
        f"https://graph.facebook.com/{version}/{os.environ['KIN_WHATSAPP_PHONE_ID']}/messages",
        headers={"Authorization": f"Bearer {os.environ['KIN_WHATSAPP_TOKEN']}"},
        json={"messaging_product": "whatsapp", "to": phone.lstrip("+"), "type": "text", "text": {"body": text[:4000]}},
        timeout=TIMEOUT,
    ).raise_for_status()


def send_family_text(contact: dict[str, Any], text: str) -> None:
    """A plain message to one family contact (not an alert)."""
    if contact["channel"] == "whatsapp":
        send_whatsapp_text(contact["address"], text)
    elif contact["channel"] == "telegram":
        httpx.post(
            f"https://api.telegram.org/bot{os.environ['KIN_TELEGRAM_BOT_TOKEN']}/sendMessage",
            json={"chat_id": contact["address"], "text": text},
            timeout=TIMEOUT,
        ).raise_for_status()
    else:
        raise ValueError(f"Can't send messages over {contact['channel']}.")


SENDERS = {"whatsapp": send_whatsapp, "telegram": send_telegram, "ntfy": send_ntfy}


def notify_family(person: dict[str, Any], level: str, reason: str) -> list[dict[str, Any]]:
    """Send an alert to every contact. Returns one result per attempted send."""
    targets = [(c["channel"], c["address"], c.get("name", "")) for c in person.get("family", [])]
    if topic := os.environ.get("KIN_NTFY_TOPIC"):
        targets.append(("ntfy", topic, "family topic"))

    results = []
    for channel, address, name in targets:
        try:
            SENDERS[channel](address, person["name"], level, reason)
            results.append({"channel": channel, "to": name, "ok": True})
        except (httpx.HTTPError, KeyError) as e:
            # KeyError: the channel's credentials aren't configured.
            detail = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else str(e)[:200]
            log.warning("alert to %s over %s failed: %s", name, channel, detail)
            results.append({"channel": channel, "to": name, "ok": False, "error": detail})
    return results
