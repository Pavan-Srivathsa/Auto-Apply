"""Is this answer to our outreach good news, and why, in one line.

Jev decides the verdict when its key is set: a three-way choice with
contrastive criteria, so "thanks, forwarding your resume to the team" and
"thanks, we will keep your resume on file" land on different sides. A
cheap text model writes the one-sentence "Why" the Slack alert shows, and
decides the verdict too when there is no Jev key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from . import jev, llm, prompts

VERDICTS = ("positive", "neutral", "negative")

CRITERIA = {
    "positive": {"what": "A real next step or open interest: forwarding the resume to the hiring team, asking "
                         "for a call or availability, inviting an application, a referral, an intro to someone",
                 "examples": ["I'm forwarding your resume to the hiring team", "Are you free Thursday for a quick chat?"]},
    "neutral": {"what": "No decision either way: an auto-reply, out of office, a bare thank you, 'will take a look', "
                        "a redirect to apply online with no further help",
                "not_for": "Forwarding to the team or proposing a time, which are positive",
                "examples": ["Thanks for reaching out, I'll take a look", "I am out of office until Monday"]},
    "negative": {"what": "A closed door: no openings, the role is filled, not a fit, keeping the resume on file, "
                         "asking not to be contacted",
                 "examples": ["The role has been filled", "Please remove me from your list"]},
}


@dataclass
class Verdict:
    verdict: str
    why: str
    confidence: Optional[float] = None


def _why_and_maybe_verdict(reply: str, role: str, verdict: str = "") -> tuple[str, str]:
    user = (f"## Role\n\n{role}\n\n## Their reply\n\n{reply}"
            + (f"\n\n## The verdict, already decided\n\n{verdict}" if verdict else ""))
    raw = llm.complete(prompts.text("reply"), user, temperature=0.1, max_tokens=300)
    match = re.search(r"```json[^\n]*\n(.*?)```", raw, re.S)
    data = json.loads(match.group(1) if match else raw)
    found = verdict or str(data.get("verdict", "")).lower()
    return (found if found in VERDICTS else "neutral"), " ".join(str(data.get("why", "")).split())[:200]


def classify(reply: str, role: str) -> Verdict:
    """Raises on a model failure; the caller tries again on the next check."""
    if jev.configured():
        answer = jev.ask({"reply": reply, "role": role}, {"verdict": jev.choice(
            {"question": "What does `reply` mean for the candidate's application to `role`?",
             "focus": "Judge what the sender will do next, not how polite they are."}, CRITERIA)})["verdict"]
        verdict = answer["choice"] if answer["choice"] in VERDICTS else "neutral"
        _, why = _why_and_maybe_verdict(reply, role, verdict)
        return Verdict(verdict, why, float(answer.get("confidence", 0.0)))
    verdict, why = _why_and_maybe_verdict(reply, role)
    return Verdict(verdict, why)


PROMPT = """You read one reply from a recruiter or hiring manager to a job candidate's email.

Say whether it is positive (a real next step or open interest: forwarding the resume to the
hiring team, proposing a call, inviting an application, a referral or an intro), neutral (an
auto-reply, out of office, a bare thank you, "will take a look") or negative (no openings, the
role is filled, not a fit, "we'll keep your resume on file", do not contact). When the verdict
is given to you, keep it.

Then write `why`: one short sentence a busy reader understands at a glance, naming the concrete
signal in the reply, e.g. "Forwarding resume to hiring team shows active interest and concrete
next step." No quotes from the email longer than a few words, no advice.

Reply with one fenced json block and nothing else:

```json
{"verdict": "positive", "why": "<one sentence>"}
```
"""
