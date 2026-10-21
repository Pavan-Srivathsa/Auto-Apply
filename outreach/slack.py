"""Positive replies, announced in Slack the way the person asked for:

    🎉 Positive reply for <you> — from <name> · <title> @ <company>
    Role / Subject / Replied to / Reply time / Reply / Why / Win #

One incoming webhook (`SLACK_WEBHOOK_URL`); the app's name and icon are
whatever the person gave the Slack app ("Autopilot Alerts"). Posting is
announcing, not answering: nothing here writes to the recruiter.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import httpx

WEBHOOK_ENV = "SLACK_WEBHOOK_URL"
REPLY_CHARS = 600


class SlackError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get(WEBHOOK_ENV, "").strip())


def _when(iso: str) -> str:
    moment = datetime.fromisoformat(iso).astimezone()
    return moment.strftime("%b %-d %-I:%M %p")


def gap(earlier_iso: str, later_iso: str) -> str:
    """"1d 18h", "5h 12m", "40m": how long the answer took."""
    seconds = max(0, int((datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso)).total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def message(*, candidate: str, name: str, title: str, company: str, role: str, subject: str,
            replied_to: str, sent_at: str, received_at: str, reply: str, why: str, win: int) -> str:
    who = " · ".join(part for part in (_escape(name), _escape(title)) if part)
    text = " ".join(reply.split())
    if len(text) > REPLY_CHARS:
        text = text[:REPLY_CHARS].rsplit(" ", 1)[0] + "…"
    return "\n".join([
        f":tada: Positive reply for {_escape(candidate)} — from {who} @ *{_escape(company)}*",
        f"*Role:* {_escape(role)}",
        f"*Subject:* {_escape(subject)}",
        f"*Replied to:* {replied_to}",
        f"*Reply time:* {_when(received_at)} · {gap(sent_at, received_at)} after {replied_to}",
        f"*Reply:* _{_escape(text)}_",
        f"*Why:* {_escape(why)}",
        f"*Win #:* {win} all-time",
    ])


def post(text: str, client: Optional[httpx.Client] = None) -> None:
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        raise SlackError(f"{WEBHOOK_ENV} is not set")
    http = client or httpx.Client(timeout=20)
    try:
        response = http.post(url, json={"text": text})
    except httpx.HTTPError as exc:
        raise SlackError(f"Slack unreachable: {exc}") from exc
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise SlackError(f"Slack answered {response.status_code}: {response.text[:120]}")
