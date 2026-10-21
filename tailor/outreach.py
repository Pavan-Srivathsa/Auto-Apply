"""Grounded recruiter outreach, prepared for a human to edit and send."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from outreach.models import Contact, Draft

from . import brief as briefs
from . import cover, critic, llm, prompts
from .answers import Context

RULES = Path(__file__).resolve().parent / "outreach_rules.md"
REPLY_FORMAT = """
## Reply format

Reply with exactly two fenced blocks and nothing else:

```subject
<subject line>
```

```email
<plain-text email>
```
"""
ATTEMPTS = 3
MIN_WORDS = 60
MAX_WORDS = 160


class OutreachError(RuntimeError):
    pass


@dataclass
class Written:
    subject: str
    body: str
    model: str
    attempts: int
    review: dict = None


def system_prompt() -> str:
    return prompts.text("outreach") + "\n" + prompts.text("outreach.format")


def _block(reply: str, name: str) -> str:
    match = re.search(rf"```{name}\s*\n(.*?)```", reply, re.S | re.I)
    return match.group(1).strip() if match else ""


def problems(subject: str, body: str) -> list[str]:
    found = []
    subject_words = len(subject.split())
    words = len(body.split())
    if not 4 <= subject_words <= 9:
        found.append(f"the subject was {subject_words} words; use 4 to 9")
    if not MIN_WORDS <= words <= MAX_WORDS:
        found.append(f"the email was {words} words; use {MIN_WORDS} to {MAX_WORDS}")
    if not re.search(r"^Best,\s*$", body, re.I | re.M):
        found.append('the email did not close with "Best," and the candidate name')
    if re.search(r"^\s*[-*#>]", body, re.M) or "**" in body:
        found.append("the email used markdown or a list")
    if cover.has_dash(body):
        found.append("the email used a dash as punctuation")
    return found


def _joined(subject: str, body: str) -> str:
    return f"Subject: {subject}\n\n{body}"


def _split(text: str) -> tuple[str, str]:
    head, _, body = text.partition("\n\n")
    return head.removeprefix("Subject:").strip(), body.strip()


def write(context: Context, contact: Contact, job_title: str, company: str,
          candidate_name: str, attach_resume: bool = True,
          model: Optional[str] = None, brief: Optional[briefs.Brief] = None) -> Written:
    if not context.posting or not context.resume_tex:
        raise OutreachError("the posting and tailored resume are required")
    if not contact.name:
        raise OutreachError("the contact needs a name")
    model = model or llm.tailor_model()
    user = context.as_message() + (
        "\n\n## Recipient\n\n"
        f"Name: {contact.name}\nTitle: {contact.title or 'unknown'}\n"
        f"Role in outreach: {contact.role.replace('_', ' ')}\n"
        f"Company: {company or contact.company}\n"
        f"Job: {job_title}\n"
        f"Candidate name: {candidate_name}\n"
        f"Tailored resume attached: {'yes' if attach_resume else 'no'}"
    )
    last_subject = last_body = ""
    last_problems = []
    for attempt in range(1, ATTEMPTS + 1):
        reply = llm.complete(
            system_prompt(), user, model=model, temperature=0.4, max_tokens=1200,
        )
        subject = " ".join(_block(reply, "subject").split())
        body = _block(reply, "email").strip()
        last_subject, last_body = subject, body
        last_problems = problems(subject, body)
        if not last_problems:
            return _refined(Written(subject, body, model, attempt), user, model, brief,
                            sources=context.resume_tex + "\n" + context.profile)
        user += (
            "\n\n## Previous reply rejected\n\n"
            + "; ".join(last_problems)
            + ". Rewrite both blocks and keep every grounded fact."
        )
    raise OutreachError(
        f"outreach draft broke the rules on {ATTEMPTS} attempts: "
        + "; ".join(last_problems)
        + f" (last subject: {last_subject!r}, body words: {len(last_body.split())})"
    )


def _refined(written: Written, user: str, model: str, brief: Optional[briefs.Brief],
             sources: str = "") -> Written:
    """Judge the email against the brief and revise while it improves
    (tailor.critic). A revision must pass `problems` like the first draft."""
    if brief is not None:
        user += "\n\n" + brief.as_message()

    def revise(text: str, fixes: list[str]) -> str:
        reply = llm.complete(system_prompt(), user + critic.revision_request(text, fixes),
                             model=model, temperature=0.4, max_tokens=1200)
        return _joined(" ".join(_block(reply, "subject").split()), _block(reply, "email").strip())

    review = critic.refine(
        "email", _joined(written.subject, written.body), brief, revise,
        check=lambda text: problems(*_split(text)),
        subject_of=lambda text: _split(text)[0], body_of=lambda text: _split(text)[1],
        sources=sources,
    )
    written.subject, written.body = _split(review.text)
    written.review = review.to_dict()
    return written


def draft(context: Context, contact: Contact, job_title: str, company: str,
          candidate_name: str, attach_resume: bool = True,
          resume_folder: str = "", model: Optional[str] = None,
          brief: Optional[briefs.Brief] = None) -> Draft:
    written = write(
        context, contact, job_title, company, candidate_name,
        attach_resume=attach_resume, model=model, brief=brief,
    )
    return Draft.create(
        contact_id=contact.id,
        subject=written.subject,
        body=written.body,
        attach_resume=attach_resume,
        resume_folder=resume_folder,
        model=written.model,
        review=written.review or {},
    )
