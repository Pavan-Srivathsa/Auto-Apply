"""Answers to our outreach, read off the Gmail inbox over IMAP.

Read-only and narrow on purpose: one login with the app password that
already sends the mail, recent messages' headers only, every fetch a
`BODY.PEEK` so nothing is marked read, and a body opened only when its
`In-Reply-To` / `References` name a Message-ID we sent. Everything else in
the inbox is never read. This module cannot send; replies are answered by
the person, and follow-ups are drafts that wait on the review page.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterable, Optional

HOST_ENV = "AUTOPILOT_IMAP_HOST"
USER_ENV = "AUTOPILOT_SMTP_USER"        # the same account and app password the mail goes out from
PASS_ENV = "AUTOPILOT_SMTP_PASS"
LOOKBACK_DAYS = 30
MAX_HEADERS = 400                       # most recent inbox messages looked at per check
REPLY_CHARS = 2000


class ReplyError(RuntimeError):
    pass


@dataclass
class Found:
    message_id: str
    answers: str          # the Message-ID of ours it answers
    sender: str
    subject: str
    received_at: str      # ISO, UTC
    text: str             # the new part of the reply, quoted history cut


def configured() -> bool:
    return bool(os.environ.get(USER_ENV, "").strip() and os.environ.get(PASS_ENV, "").strip())


def _ids(value: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def _decoded(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001 - a header we cannot decode is shown raw
        return value or ""


QUOTE_START = re.compile(
    r"^(On .{5,200}wrote:\s*$|-{2,} ?Original Message ?-{2,}|From: .+|Sent from my )", re.I | re.M)


def new_text(body: str) -> str:
    """The reply without the thread it quotes and without the signature
    boilerplate that follows "On ... wrote:"."""
    lines = []
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    text = "\n".join(lines)
    match = QUOTE_START.search(text)
    if match:
        text = text[:match.start()]
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:REPLY_CHARS]


def _plain(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>", "\n", html))
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def find(sent_ids: Iterable[str], since: Optional[datetime] = None, imap=None) -> list[Found]:
    """Replies to any of `sent_ids` in the inbox. Raises ReplyError."""
    ours = {i.strip() for i in sent_ids if i and i.strip()}
    if not ours:
        return []
    if imap is None and not configured():
        raise ReplyError("mail is not set up: AUTOPILOT_SMTP_USER, AUTOPILOT_SMTP_PASS in .env")
    since = since or datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    own = imap is None
    try:
        if own:
            imap = imaplib.IMAP4_SSL(os.environ.get(HOST_ENV, "imap.gmail.com"))
            imap.login(os.environ[USER_ENV].strip(), os.environ[PASS_ENV])
        imap.select("INBOX", readonly=True)
        status, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        if status != "OK":
            raise ReplyError(f"IMAP search failed: {status}")
        numbers = (data[0] or b"").split()[-MAX_HEADERS:]
        found: list[Found] = []
        for number in numbers:
            status, rows = imap.fetch(number, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES FROM SUBJECT DATE)])")
            if status != "OK" or not rows or not isinstance(rows[0], tuple):
                continue
            head = email.message_from_bytes(rows[0][1])
            answered = [i for i in _ids(head.get("In-Reply-To", "")) + _ids(head.get("References", "")) if i in ours]
            if not answered:
                continue
            status, rows = imap.fetch(number, "(BODY.PEEK[])")
            if status != "OK" or not rows or not isinstance(rows[0], tuple):
                continue
            full = email.message_from_bytes(rows[0][1])
            try:
                when = parsedate_to_datetime(head.get("Date")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                when = datetime.now(timezone.utc)
            found.append(Found(
                message_id=(_ids(head.get("Message-ID", "")) or [f"<imap-{number.decode()}>"])[0],
                answers=answered[0],
                sender=_decoded(head.get("From", "")),
                subject=_decoded(head.get("Subject", "")),
                received_at=when.isoformat(timespec="seconds"),
                text=new_text(_plain(full)),
            ))
        return found
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ReplyError(f"IMAP failed: {exc}") from exc
    finally:
        if own and imap is not None:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass


def sender_address(sender: str) -> str:
    return parseaddr(sender)[1].lower()
