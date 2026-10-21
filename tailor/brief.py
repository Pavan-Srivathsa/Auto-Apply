"""The job brief: what the posting asks for, and what the candidate has that
answers it, decided once and used by every document.

Before this, the resume, the letter and each email re-read the posting on
their own and each picked its own proof, so they could disagree and nothing
said which requirements the application actually answered. The brief is one
cheap model call that ranks the posting's requirements and, for each, quotes
the line of the resume or a story that answers it, or says there is none.
Code then checks every quote against the sources: a quote that is not
there is dropped to a gap, so the brief cannot invent evidence, which is
the rule the whole tailoring side keeps. With a Jev key, Jev then judges
each quote against its requirement (`judge`): an adjacent skill offered as
the skill reads as a stretch and becomes a gap too. The brief is what the writers are
told to build on, what the critic judges coverage against, and what the
review page shows as "what they want / what you have".
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import jev, llm, prompts

RULES = Path(__file__).resolve().parent / "brief_rules.md"
FILE = "brief.json"
MAX_REQUIREMENTS = 8
MAX_POSTING = 14000       # characters of posting the model reads; the rest is boilerplate
STRETCH = 0.5             # Jev's probability below which quoted evidence does not meet the requirement


class BriefError(RuntimeError):
    pass


@dataclass
class Requirement:
    text: str                       # what the posting asks, in its own words
    kind: str = "must"              # "must" | "nice"
    terms: list[str] = field(default_factory=list)   # the posting's exact spellings to look for
    evidence: str = ""              # quote from the resume or a story; "" = a gap
    where: str = ""                 # "resume" | "profile"
    strength: Optional[float] = None   # Jev: probability the evidence meets it; None = not judged

    @property
    def gap(self) -> bool:
        return not self.evidence


@dataclass
class Brief:
    role: str = ""
    requirements: list[Requirement] = field(default_factory=list)
    model: str = ""

    def musts(self) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == "must"]

    def as_message(self) -> str:
        """The section the writers are given. Gaps are named so a writer
        stays silent on them instead of reaching."""
        lines = ["## What this job asks for, and what the candidate has", ""]
        for i, req in enumerate(self.requirements, 1):
            head = f"{i}. [{'required' if req.kind == 'must' else 'nice to have'}] {req.text}"
            lines.append(head)
            lines.append(f"   Evidence: \"{req.evidence}\"" if req.evidence
                         else "   Evidence: none on file. Do not claim it; say nothing about it.")
        lines += ["", "Build on the required items that have evidence. Use the evidence as given; "
                      "never add a fact that is not in it or in the resume."]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"role": self.role, "model": self.model, "requirements": [asdict(r) for r in self.requirements]}


# ------------------------------------------------------------ matching --
def loose(text: str) -> str:
    """Lowercase letters and digits, one space between runs: the form two
    spellings of the same line compare equal in."""
    words = (w.strip(".") for w in re.findall(r"[a-z0-9+#.]+", text.lower()))
    return " ".join(w for w in words if w)          # "Airflow." is "airflow"; "node.js" stays


def _plain(tex: str) -> str:
    """A LaTeX source as the words a reader sees."""
    text = re.sub(r"\\href\{[^}]*\}", "", tex)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    return re.sub(r"[{}\\%$&#_^~]", " ", text)


def mentions(source: str, term: str) -> bool:
    """Does `source` (plain text or LaTeX) say `term`, as a whole word run."""
    needle = loose(term)
    return bool(needle) and f" {needle} " in f" {loose(_plain(source))} "


def grounded(quote: str, *sources: str) -> Optional[str]:
    """The name of the source the quote really appears in, else None. A
    quote is matched loosely and whole: no stitching of two lines."""
    needle = loose(quote)
    if len(needle) < 8:
        return None
    for name, source in zip(("resume", "profile"), sources):
        if source and needle in loose(_plain(source)):
            return name
    return None


def covered(req: Requirement, document: str) -> bool:
    """Does the finished document speak to this requirement: any of the
    posting's terms for it, or the words of the requirement's evidence."""
    if any(mentions(document, term) for term in req.terms):
        return True
    return bool(req.evidence) and loose(req.evidence) in loose(_plain(document))


def coverage(brief: Brief, document: str) -> dict:
    """Which requirements a document answers. `musts` counts only the
    required ones that have evidence: a gap cannot be covered honestly."""
    rows = [{"text": r.text, "kind": r.kind, "gap": r.gap, "covered": covered(r, document)} for r in brief.requirements]
    answerable = [r for r in rows if r["kind"] == "must" and not r["gap"]]
    return {"rows": rows, "musts_answerable": len(answerable),
            "musts_covered": sum(1 for r in answerable if r["covered"])}


# --------------------------------------------------------------- parse --
def parse(reply: str, resume: str, profile: str, model: str = "") -> Brief:
    match = re.search(r"```json[^\n]*\n(.*?)```", reply, re.S)
    try:
        data = json.loads(match.group(1) if match else reply)
    except json.JSONDecodeError as exc:
        raise BriefError(f"brief reply is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
        raise BriefError("brief reply has no requirements list")
    reqs: list[Requirement] = []
    for item in data["requirements"]:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        evidence = str(item.get("evidence") or "").strip()
        where = grounded(evidence, resume, profile) if evidence else None
        reqs.append(Requirement(
            text=" ".join(str(item["text"]).split())[:240],
            kind="nice" if str(item.get("kind", "")).lower().startswith("n") else "must",
            terms=[str(t).strip() for t in (item.get("terms") or []) if str(t).strip()][:6],
            evidence=evidence if where else "",       # a quote that is not there is a gap
            where=where or "",
        ))
    if not reqs:
        raise BriefError("brief reply had no usable requirements")
    reqs.sort(key=lambda r: r.kind != "must")         # stable: the model's own ranking within a kind
    return Brief(role=str(data.get("role", "")).strip()[:120], requirements=reqs[:MAX_REQUIREMENTS], model=model)


def judge(brief: Brief) -> Brief:
    """Jev reads each requirement next to its quote and says how likely the
    quote meets it. One request, every question in parallel. Below STRETCH
    the evidence is dropped and the requirement is a gap. Without a key, or
    when Jev fails, the brief is returned as the text model left it."""
    rows = [(i, r) for i, r in enumerate(brief.requirements) if r.evidence]
    if not rows or not jev.configured():
        return brief
    state = {"requirements": [{"asks": r.text, "evidence": r.evidence} for _, r in rows]}
    questions = {
        f"r{n}": jev.noul(
            {"question": f"Does `requirements[{n}].evidence` show the candidate has what "
                         f"`requirements[{n}].asks` asks for?",
             "focus": "The same skill or work, done for real. An adjacent or merely related skill is not it."},
            {"true": "The evidence is the thing asked for, or clearly includes it",
             "false": "The evidence is related, adjacent, or a different thing"})
        for n in range(len(rows))
    }
    try:
        answers = jev.ask(state, questions)
    except jev.JevError:
        return brief
    for n, (_, req) in enumerate(rows):
        req.strength = round(float(answers[f"r{n}"].get("noul", 0.0)), 3)
        if req.strength < STRETCH:
            req.evidence, req.where = "", ""
    return brief


def build(posting: str, resume_tex: str, profile: str = "", model: Optional[str] = None) -> Brief:
    """One text call to read the posting, then Jev to judge the evidence.
    Raises BriefError / LLMError; callers treat a brief as an extra and
    carry on without one."""
    model = model or llm.tailor_model()
    user = (f"## Job posting\n\n{posting[:MAX_POSTING]}\n\n"
            f"## Candidate resume (LaTeX)\n\n```tex\n{resume_tex}\n```\n\n"
            f"## Candidate profile and stories\n\n{profile or '(none)'}")
    reply = llm.complete(prompts.text("brief") + "\n" + prompts.text("brief.format"), user,
                         model=model, temperature=0.1, max_tokens=3000)
    return judge(parse(reply, resume_tex, profile, model))


# ------------------------------------------------------------- storage --
def save(app_dir: Path, brief: Brief) -> None:
    (app_dir / FILE).write_text(json.dumps(brief.to_dict(), indent=2) + "\n")


def load(app_dir: Path) -> Optional[Brief]:
    try:
        data = json.loads((app_dir / FILE).read_text())
        return Brief(role=data.get("role", ""), model=data.get("model", ""),
                     requirements=[Requirement(**r) for r in data.get("requirements", [])])
    except (OSError, ValueError, TypeError):
        return None


REPLY_FORMAT = """
## Reply format

Reply with one fenced json block and nothing else:

```json
{"role": "<the job title as the posting writes it>",
 "requirements": [
   {"text": "<what the posting asks for, short, its own words>",
    "kind": "must" or "nice",
    "terms": ["<exact skill, tool or phrase the posting uses for it>"],
    "evidence": "<a line copied exactly from the resume or profile that answers it, or an empty string>"}
 ]}
```
"""
