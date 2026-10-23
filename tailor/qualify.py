"""Am I qualified for this job, and if not, why: one verdict, one line each.

The brief says which line of the resume answers each requirement, or that
none does. That is too black and white to explain a "no": a candidate whose
incident agent correlates logs, metrics and traces is not "missing"
observability, they are partly there. Here every requirement is rated
against everything the candidate has (resume and stories) on three levels,
meets / partly / missing, by Jev when its key is set (one request, every
requirement in parallel), else by the text model. A cheap text model then
writes one plain line of reason for each requirement that is not fully met.

The overall call is made in code, so it can be read and argued with:

- not_qualified: a hard blocker on the screen (years, clearance,
  citizenship...), at least half the required items missing, or none of
  three or more fully met;
- stretch: any required item missing, or half of them only partly met;
- qualified: otherwise.

It never skips a job. A not_qualified job is still tailored, and waits at
checkpoint 1 for the person instead of approving itself.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import brief as briefs
from . import jev, llm, prompts

FILE = "qualify.json"
MISSING_AT = 0.30      # Jev's probability of "missing" at which an item is missing: doubt counts against
MEETS_AT = 0.50        # ... of "meets" at which it is met
LEVELS = ("missing", "partly", "meets")
VERDICTS = ("qualified", "stretch", "not_qualified")
LABEL = {"qualified": "Qualified", "stretch": "Stretch", "not_qualified": "Not qualified"}


@dataclass
class Item:
    text: str
    kind: str                      # must | nice
    level: str                     # missing | partly | meets
    reason: str = ""
    confidence: Optional[float] = None


@dataclass
class Assessment:
    verdict: str
    summary: str
    items: list[Item] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    judge: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- levels --
def _sources(resume: str, stories: str) -> dict:
    return {name: " ".join(briefs._plain(text).split())
            for name, text in (("resume", resume), ("stories", stories)) if text and text.strip()}


def levels_jev(brief: briefs.Brief, resume: str, stories: str) -> list[tuple[str, float]]:
    state = {"requirements": [r.text for r in brief.requirements], "sources": _sources(resume, stories)}
    questions = {
        f"r{i}": jev.score(
            {"question": f"How fully do `sources.resume` and `sources.stories` show what `requirements[{i}]` asks for?",
             "focus": "The same skill or work done for real counts. A named language, framework or tool counts "
                      "only if that one, or a direct equivalent, appears: another language is not partly TypeScript. "
                      "Related work of a smaller scope is partly. Years count only as the dates show them."},
            ["Missing: nothing in the sources shows it", "Partly: related work, or less of it than asked",
             "Meets: the sources clearly show it"])
        for i in range(len(brief.requirements))
    }
    answers = jev.ask(state, questions)
    return [(level_of(answers[f"r{i}"]), float(answers[f"r{i}"].get("confidence", 0.0)))
            for i in range(len(brief.requirements))]


def level_of(answer: dict) -> str:
    """From Jev's distribution, not its rounded mean: rounding 37% "missing"
    and 62% "partly" said partly TypeScript for a candidate with none."""
    probs = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if probs:
        if probs.get("0", 0.0) >= MISSING_AT:
            return "missing"
        return "meets" if probs.get("2", 0.0) >= MEETS_AT else "partly"
    value = float(answer["score"])
    return "missing" if value < 0.75 else "meets" if value >= 1.5 else "partly"


def _json(text: str):
    match = re.search(r"```json[^\n]*\n(.*?)```", text, re.S)
    return json.loads(match.group(1) if match else text)


def reasons(brief: briefs.Brief, resume: str, stories: str, levels: Optional[list[str]] = None) -> dict:
    """One line per requirement (and the levels too when none are given)."""
    rows = [{"i": i, "asks": r.text, **({"level": levels[i]} if levels else {})}
            for i, r in enumerate(brief.requirements)]
    user = (json.dumps({"requirements": rows, "sources": _sources(resume, stories)}, indent=1)
            + ("\n\nThe levels are decided; keep them and write the reasons."
               if levels else "\n\nDecide each level and write the reasons."))
    data = _json(llm.complete(prompts.text("qualify"), user, temperature=0.1, max_tokens=1500))
    return {int(row["i"]): row for row in data.get("requirements", []) if isinstance(row, dict) and "i" in row}


# -------------------------------------------------------------- verdict --
def blockers(screen: Optional[dict]) -> list[str]:
    """Hard flags the on-page screen found (years, clearance, citizenship...)."""
    if not screen:
        return []
    return [f"{f.get('category', 'other')}: {f.get('reason') or f.get('quote', '')}".strip()
            for f in screen.get("flags", []) if f.get("severity") == "hard"]


def verdict(items: list[Item], hard: list[str]) -> tuple[str, str]:
    musts = [i for i in items if i.kind == "must"] or items
    missing = [i for i in musts if i.level == "missing"]
    partly = [i for i in musts if i.level == "partly"]
    half = math.ceil(len(musts) / 2)
    names = lambda rows: ", ".join(r.text.split(",")[0][:60] for r in rows[:4])
    if hard:
        return "not_qualified", f"Not qualified: {hard[0]}"
    if len(musts) >= 3 and not any(i.level == "meets" for i in musts):
        return "not_qualified", (f"Not qualified: none of the {len(musts)} required items fully met"
                                 + (f"; {len(missing)} missing ({names(missing)})." if missing else "."))
    if missing and len(missing) >= half:
        return "not_qualified", f"Not qualified: {len(missing)} of {len(musts)} required items missing ({names(missing)})."
    if missing:
        return "stretch", f"Stretch: {len(missing)} of {len(musts)} required items missing ({names(missing)})."
    if partly and len(partly) >= half:
        return "stretch", f"Stretch: {len(partly)} of {len(musts)} required items only partly met ({names(partly)})."
    return "qualified", f"Qualified: meets {len(musts) - len(partly)} of {len(musts)} required items" + (
        f", partly {len(partly)} ({names(partly)})." if partly else ".")


def assess(brief: briefs.Brief, resume: str, stories: str = "", screen: Optional[dict] = None,
           explain: bool = True) -> Assessment:
    """Raises on a model failure; the caller treats the verdict as an extra.
    `explain=False` skips the one-line reason per gap when Jev decides the
    levels: the levels and the verdict are the same, the call that writes the
    reasons (about half a Scout fit's cost, 2026-10-23) is not made."""
    judge = "jev" if jev.configured() else llm.tailor_model()
    if jev.configured():
        rated = levels_jev(brief, resume, stories)
        lines = reasons(brief, resume, stories, [level for level, _ in rated]) if explain else {}
    else:
        lines = reasons(brief, resume, stories)
        rated = [(str(lines.get(i, {}).get("level", "missing")), None) for i in range(len(brief.requirements))]
    items = []
    for i, r in enumerate(brief.requirements):
        level = rated[i][0] if rated[i][0] in LEVELS else "missing"
        items.append(Item(text=r.text, kind=r.kind, level=level, confidence=rated[i][1],
                          reason="" if level == "meets" else " ".join(str(lines.get(i, {}).get("reason", "")).split())[:220]))
    hard = blockers(screen)
    call, summary = verdict(items, hard)
    return Assessment(verdict=call, summary=summary, items=items, blockers=hard, judge=judge)


def save(app_dir: Path, assessment: Assessment) -> None:
    (app_dir / FILE).write_text(json.dumps(assessment.to_dict(), indent=2) + "\n")


def load(app_dir: Path) -> Optional[dict]:
    try:
        return json.loads((app_dir / FILE).read_text())
    except (OSError, ValueError):
        return None


PROMPT = """You tell a job candidate, plainly and kindly, where they fall short of one job.

You get the job's requirements and everything the candidate has (`sources.resume`,
`sources.stories`). For each requirement give `level`: "meets" (the sources clearly show it),
"partly" (related work, or less of it than asked) or "missing" (nothing related). When levels
are given to you, keep them.

For every requirement that is not "meets", write `reason`: one short sentence naming what is
there and what is not, e.g. "You use Python and C++ in production; no TypeScript or Go." or
"Kubernetes and Docker deployments, but no object storage or cloud infrastructure ownership."
Plain, specific, no advice, no softening words. Years count only as the dates show them.

Reply with one fenced json block and nothing else:

```json
{"requirements": [{"i": 0, "level": "partly", "reason": "<one sentence>"}]}
```
"""
