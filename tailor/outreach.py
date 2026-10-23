"""Grounded recruiter outreach, prepared for a human to edit and send."""

from __future__ import annotations

import difflib
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


SIMILAR = 0.55        # a draft this close to a colleague's (difflib ratio) is a copy, not a second email


def first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0].strip(",.")


def _core(body: str) -> str:
    """The email without its greeting and sign-off, for comparing two."""
    lines = [l for l in body.strip().splitlines() if l.strip()]
    return " ".join(lines[1:-2]) if len(lines) > 3 else " ".join(lines)


def problems(subject: str, body: str, first: str = "", peers: tuple = ()) -> list[str]:
    """What the code can see is wrong. `first` is the reader's first name;
    `peers` are (name, body) of the emails already written to colleagues at
    the same company, which this one must not repeat."""
    found = []
    subject_words = len(subject.split())
    words = len(body.split())
    if not 4 <= subject_words <= 9:
        found.append(f"the subject was {subject_words} words; use 4 to 9")
    if first and not subject.lower().startswith(first.lower()):
        # The person's screenshot: "Morgan, I would love to join Globex".
        found.append(f'the subject must open with "{first}," and then a specific hook, '
                     f'e.g. "{first}, evals for clinical agents", never only the role and company')
    if first and not re.match(rf"^\s*Hi {re.escape(first)},\s*$", body.splitlines()[0] if body else "", re.I):
        found.append(f'the email must open with "Hi {first}," on its own line')
    if not MIN_WORDS <= words <= MAX_WORDS:
        found.append(f"the email was {words} words; use {MIN_WORDS} to {MAX_WORDS}")
    if not re.search(r"^Best,\s*$", body, re.I | re.M):
        found.append('the email did not close with "Best," and the candidate name')
    if re.search(r"^\s*[-*#>]", body, re.M) or "**" in body:
        found.append("the email used markdown or a list")
    if cover.has_dash(body):
        found.append("the email used a dash as punctuation")
    mine_open, mine_figures = _opening(body), _figures(body)
    for name, other in peers:
        if difflib.SequenceMatcher(None, _core(body).lower(), _core(other).lower()).ratio() > SIMILAR:
            found.append(f"the email is too close to the one already written to {name}; they work together and "
                         "will compare: open differently and use a different proof point")
            break
        if difflib.SequenceMatcher(None, mine_open, _opening(other)).ratio() > OPEN_SIMILAR:
            found.append(f"the first sentence is the same idea as the email to {name} opens with; open differently")
            break
        shared = mine_figures & _figures(other)
        if len(shared) >= 2:
            found.append(f"the proof point is the one already used for {name} ({', '.join(sorted(shared))}); "
                         "use a different achievement for this person")
            break
    return found


OPEN_SIMILAR = 0.6


def _opening(body: str) -> str:
    """The first sentence after the greeting, lowercased."""
    core = _core(body)
    return re.split(r"(?<=[.!?])\s+", core, maxsplit=1)[0].lower() if core else ""


def _figures(body: str) -> set[str]:
    """The numbers an email states: the same ones mean the same proof point."""
    return {m.group(0).rstrip("%") for m in re.finditer(r"\d[\d,.]*%?", _core(body)) if len(m.group(0)) > 1}


def _joined(subject: str, body: str) -> str:
    return f"Subject: {subject}\n\n{body}"


def _split(text: str) -> tuple[str, str]:
    head, _, body = text.partition("\n\n")
    return head.removeprefix("Subject:").strip(), body.strip()


def write(context: Context, contact: Contact, job_title: str, company: str,
          candidate_name: str, attach_resume: bool = True,
          model: Optional[str] = None, brief: Optional[briefs.Brief] = None,
          peers: tuple = ()) -> Written:
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
    ) + ("".join(
        ["\n\n## Already written to colleagues at this company\n\nThey work together and will compare. "
         "Open differently and use a different proof point from the ones below.\n"]
        + [f"\n### To {name}\n{body}\n" for name, body in peers]) if peers else "")
    first = first_name(contact.name)
    last_subject = last_body = ""
    last_problems = []
    for attempt in range(1, ATTEMPTS + 1):
        reply = llm.complete(
            system_prompt(), user, model=model, temperature=0.4, max_tokens=1200,
        )
        subject = " ".join(_block(reply, "subject").split())
        body = _block(reply, "email").strip()
        last_subject, last_body = subject, body
        last_problems = problems(subject, body, first, peers)
        if not last_problems:
            return _refined(Written(subject, body, model, attempt), user, model, brief,
                            sources={"resume": context.resume_tex, "stories": context.profile,
                                     "posting": context.posting},
                            check=lambda text: problems(*_split(text), first, peers))
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
             sources: Optional[dict] = None, check=None) -> Written:
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
        check=check or (lambda text: problems(*_split(text))),
        subject_of=lambda text: _split(text)[0], body_of=lambda text: _split(text)[1],
        sources=sources,
    )
    written.subject, written.body = _split(review.text)
    written.review = review.to_dict()
    return written


def draft(context: Context, contact: Contact, job_title: str, company: str,
          candidate_name: str, attach_resume: bool = True,
          resume_folder: str = "", model: Optional[str] = None,
          brief: Optional[briefs.Brief] = None, peers: tuple = ()) -> Draft:
    written = write(
        context, contact, job_title, company, candidate_name,
        attach_resume=attach_resume, model=model, brief=brief, peers=peers,
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


# -------------------------------------------------------------- follow-up --
FOLLOWUP_WORDS = (35, 100)

FOLLOWUP_PROMPT = """You write a short follow-up from a job candidate to someone who has not answered
their first email about a role. It goes in the same thread, under the earlier message.

- 40 to 90 words. Plain text, no markdown, no lists, no dashes used as punctuation.
- Open by naming the role, not by apologising or "bumping". Never "just following up", "circling
  back", "bumping this", "I know you're busy", or any guilt.
- Add ONE new, true reason to talk: a different item from the brief's evidence than the earlier
  email used, stated with its real measure. Never repeat the earlier proof point.
- Keep the same small ask: are they the right person, or could they point the candidate to the
  hiring team. One question.
- Use only facts in the brief's evidence, the resume and the profile. Invent nothing.
- Close with "Best," and the candidate's name.

Reply with exactly one fenced block and nothing else:

```email
<plain-text email>
```
"""


def followup_problems(body: str) -> list[str]:
    found = []
    words = len(body.split())
    low, high = FOLLOWUP_WORDS
    if not low <= words <= high:
        found.append(f"the follow-up was {words} words; use {low} to {high}")
    if not re.search(r"^Best,\s*$", body, re.I | re.M):
        found.append('the follow-up did not close with "Best," and the candidate name')
    if re.search(r"^\s*[-*#>]", body, re.M) or "**" in body:
        found.append("the follow-up used markdown or a list")
    if cover.has_dash(body):
        found.append("the follow-up used a dash as punctuation")
    if re.search(r"follow(ing)?[ -]up on|following up|circling back|bump(ing)? this|i know you'?re busy|"
                 r"checking in|touching base", body, re.I):
        found.append("the follow-up used a guilt or bump phrase")
    return found


def write_followup(context: Context, contact: Contact, earlier: list[Draft], n: int,
                   candidate_name: str, brief: Optional[briefs.Brief] = None,
                   model: Optional[str] = None) -> Written:
    """Follow-up `n` to the thread `earlier` (oldest first). Its subject is the
    first email's, as a reply. Raises OutreachError after ATTEMPTS."""
    if not earlier:
        raise OutreachError("a follow-up needs the email it follows")
    model = model or llm.tailor_model()
    thread = "\n\n".join(f"### {'First email' if d.followup == 0 else f'Follow-up {d.followup}'}\n\n{d.body}"
                          for d in earlier)
    user = (context.as_message() + ("\n\n" + brief.as_message() if brief else "")
            + f"\n\n## Recipient\n\nName: {contact.name}\nTitle: {contact.title or 'unknown'}"
            + f"\nCandidate name: {candidate_name}\n\n## The thread so far, no answer yet\n\n{thread}"
            + f"\n\nWrite follow-up {n}.")
    last: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        reply = llm.complete(prompts.text("outreach.followup"), user, model=model, temperature=0.4, max_tokens=800)
        body = _block(reply, "email").strip()
        last = followup_problems(body)
        if not last:
            subject = earlier[0].subject
            return Written(subject if subject.lower().startswith("re:") else f"Re: {subject}", body, model, attempt)
        user += "\n\n## Previous reply rejected\n\n" + "; ".join(last) + ". Write it again."
    raise OutreachError(f"follow-up broke the rules on {ATTEMPTS} attempts: " + "; ".join(last))


# ------------------------------------------------------------- LinkedIn --
LINKEDIN_MAX = 300      # LinkedIn's limit on a connection-request note
LINKEDIN_PROMPT = """You write a LinkedIn connection-request note from a job candidate to one person
at a company about one role. At most 280 characters including spaces. Plain text.

- Open with "Hi <first name>," and say who the candidate is in a few words.
- One concrete, true proof point from the brief's evidence or the resume, with its real measure.
- Name the role. One small ask: whether they are open to connecting, or who runs hiring for it.
- No links, no dashes used as punctuation, no flattery, no "I hope this finds you well".
- Use only facts in the evidence, the resume and the profile. Invent nothing.

Reply with exactly one fenced block and nothing else:

```note
<the note>
```
"""


def linkedin_problems(note: str, first: str = "") -> list[str]:
    found = []
    if len(note) > LINKEDIN_MAX:
        found.append(f"the note was {len(note)} characters; LinkedIn allows {LINKEDIN_MAX}")
    if first and not re.match(rf"^\s*Hi {re.escape(first)},", note, re.I):
        found.append(f'the note must open with "Hi {first},"')
    if re.search(r"https?://|www\.", note):
        found.append("the note had a link")
    if cover.has_dash(note):
        found.append("the note used a dash as punctuation")
    return found


def write_linkedin(context: Context, contact: Contact, job_title: str, company: str,
                   candidate_name: str, brief: Optional[briefs.Brief] = None,
                   model: Optional[str] = None) -> Written:
    """A connection note: drafted, checked, and copied by hand. Never sent."""
    model = model or llm.tailor_model()
    first = first_name(contact.name)
    user = (context.as_message() + ("\n\n" + brief.as_message() if brief else "")
            + f"\n\n## Recipient\n\nName: {contact.name}\nTitle: {contact.title or 'unknown'}\n"
            + f"Company: {company or contact.company}\nJob: {job_title}\nCandidate name: {candidate_name}")
    last: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        reply = llm.complete(prompts.text("outreach.linkedin"), user, model=model, temperature=0.4, max_tokens=500)
        note = " ".join(_block(reply, "note").split())
        last = linkedin_problems(note, first)
        if not last:
            return Written("", note, model, attempt)
        user += "\n\n## Previous reply rejected\n\n" + "; ".join(last) + ". Write it again."
    raise OutreachError("LinkedIn note broke the rules on 3 attempts: " + "; ".join(last))
