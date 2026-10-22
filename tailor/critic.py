"""The loop: write, judge against the job brief, revise, keep the best.

The writers already retry on rules the code can check (word counts, dashes,
the sign-off). That finds a document that is legal, not one that is good.
This module adds the judgement a careful reader applies, as a rubric per
kind of document: a short list of named points, each a narrow question.

The judge is Jev when `TYPESAFE_API_KEY` is set: one request scores every
point on three levels, in parallel, with the same answer for the same text
(Jev is built to be self-consistent, which is what a loop that keeps only
improvements needs). Without the key a text model reads the same rubric and
answers in JSON. Either way coverage of the brief is counted in code, and
the fixes the writer gets are fixed sentences tied to the weak points, so
the loop never depends on the judge writing good advice.

A revision counts only when it passes the writer's own checks and its exact
rubric mean beats the draft's by MARGIN (the 1..10 score rounds real gains away); on any failure the best text so far is returned. So the
loop can only improve a document, and every round is in the trace for the
review page.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from . import brief as briefs
from . import jev, llm, prompts

ROUNDS = 2             # revisions after the first draft
THRESHOLD = 10         # stop early only at a perfect score; otherwise while any point needs a fix
FIX_BELOW = 1.5        # a point under this (0..2) gets its fix; "fair" is not good enough to send
TOP = {"email": 1, "cover": 3, "resume": 8}   # the brief's top required items a document must answer
INVENTED = 0.5         # Jev's probability above which a draft claims something it may not
INVENTED_CAP = 3       # the score an inventing draft cannot rise above
MARGIN = 0.03          # on the 0..2 mean: a revision must beat the draft by this much, not by noise


@dataclass(frozen=True)
class Point:
    name: str
    question: str
    levels: tuple[str, str, str]   # weak, fair, strong
    fix: str                       # the instruction the writer gets when this point is weak


EMAIL = (
    Point("opening", "Does the first line of `draft` give this stranger a specific, true reason to read on?",
          ("Generic opener: 'I came across', 'I am writing to', 'I hope this finds you well'",
           "Names the role but nothing specific to the reader or company",
           "A specific, true hook tied to this role, team or company in the first line"),
          "Rewrite the first line so it gives one specific, true reason this role and team caught the "
          "candidate's attention. No 'I came across' or 'I am writing to'."),
    Point("proof", "Does `draft` give one proof point that matches the job's top requirement, with its real measure?",
          ("No concrete proof, or a list of skills", "A proof point without a measure, or not the role's top need",
           "One concrete achievement, with its measure, matching the first requirement in `brief`"),
          "Replace any skill list with ONE achievement from the brief's evidence for the first required item, "
          "stated with its real measure, in one sentence."),
    Point("ask", "How small and clear is the request `draft` makes of the reader?",
          ("No ask, several asks, or a demand ('refer me', 'any openings')",
           "One ask, but vague or effortful", "One small, easy ask: right person, point to the team, or a short call"),
          "End with one small, easy ask: whether they are the right person, or whether they could point the "
          "candidate to the hiring team. One question, nothing more."),
    Point("voice", "Does `draft` read like one person writing to another?",
          ("Template or marketing phrasing, clichés, flattery",
           "Mostly plain with a stiff or generic sentence", "Plain, warm, specific; reads naturally aloud"),
          "Cut every cliché and compliment; write short plain sentences a person would say out loud."),
    Point("subject", "How specific is `subject`?",
          ("Generic ('Job application', 'Opportunity') or clickbait",
           "Names the role but nothing more", "Specific to this role and sender in 4 to 9 words, e.g. the reader's name and the role"),
          "Write a 4 to 9 word subject that names the role and is specific to this reader; no clickbait."),
)

COVER = (
    Point("requirements", "Does `draft` answer the top required items in `brief` with their evidence?",
          ("Answers none of them, or only in general terms", "Answers one, or answers without the evidence",
           "Answers the two or three strongest with the evidence given, in the posting's words"),
          "Build a paragraph on each of the two strongest required items in the brief, each with its quoted "
          "evidence, using the posting's own words for the work."),
    Point("why_here", "Does `draft` give a specific, true reason for this company and role?",
          ("No reason, or flattery", "A reason any company would fit", "One specific reason tied to what this company does"),
          "Give one specific, true reason this company's work fits the candidate, taken from the posting, not praise."),
    Point("concrete", "How concrete is `draft`?",
          ("Claims experience in general ('I have experience in')", "Mixes concrete work and general claims",
           "Says what was built or done and what it achieved"),
          "Replace every general claim ('I have experience in', 'I am skilled at') with what was actually built "
          "and what it achieved, from the brief's evidence."),
    Point("voice", "Does `draft` read like the candidate wrote it in one sitting?",
          ("Template phrasing, clichés", "Mostly plain with stiff sentences", "Plain, direct, varied sentences, no cliché"),
          "Rewrite stiff or template sentences in plain first person; cut 'excited', 'passionate', 'leverage', "
          "'fit' and the like."),
)

RESUME = (
    Point("requirements", "Are the top required items in `brief` visible in `draft`, in the posting's own words?",
          ("Most are missing, or only in the skills list", "Some lead their section; others are buried or reworded",
           "Each one with evidence is visible near the top of its section, using the posting's terms"),
          "Bring the required items that have evidence to the top of their sections, named in the posting's "
          "words, without adding any fact."),
    Point("bullets", "Do the bullets in `draft` lead with what was achieved, with a real measure?",
          ("Duties ('Responsible for', 'Worked on'), no results", "Mixed: some results, some duties",
           "Each bullet leads with the result or a strong verb and keeps its real measure"),
          "Rewrite duty-style bullets to lead with the result and its measure from the master resume, keeping "
          "each bullet's printed length."),
    Point("order", "Do the summary, the skills order and the bullet order in `draft` put what this job wants first?",
          ("Generic order; what the job wants is buried", "Partly reordered",
           "Summary, skills and entries lead with what this job asks for"),
          "Reorder the summary, the skills lines, and the bullets inside each role so what this job asks "
          "for comes first. Keep the roles themselves in their date order."),
)

RUBRICS = {"email": EMAIL, "cover": COVER, "resume": RESUME}


@dataclass
class Critique:
    score: int                                       # 1..10
    points: dict = field(default_factory=dict)       # point name -> 0..2
    fixes: list[str] = field(default_factory=list)
    judge: str = ""                                  # "jev" | the text model's name
    invented: Optional[float] = None
    raw: float = 0.0                                 # the exact 0..2 mean behind `score`; what the loop compares


@dataclass
class Refined:
    text: str
    score: int = 0            # 0 = never judged (no judge was available)
    first_score: int = 0
    rounds: int = 0           # revisions that were kept
    judge: str = ""
    trace: list[dict] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return any(row.get("kept") for row in self.trace)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "text"} | {"improved": self.improved}


# ------------------------------------------------------------- judging --
def _missing(kind: str, brief: Optional[briefs.Brief], document: str) -> list[briefs.Requirement]:
    """The job's top required items with evidence that the document does not
    answer. Only the top ones: an email makes one point, a letter three, and
    the sixth requirement (often the degree) is the resume's job."""
    if not brief:
        return []
    top = [r for r in brief.musts() if not r.gap][:TOP.get(kind, 3)]
    return [r for r in top if not briefs.covered(r, document)]


def _coverage_fixes(kind: str, brief: Optional[briefs.Brief], document: str) -> list[str]:
    fixes = [f'Answer the required item "{r.text}" with this evidence: "{r.evidence}".'
             for r in _missing(kind, brief, document)]
    if kind == "resume" and brief:
        # The ATS side: the posting's exact term, missing from a resume whose
        # evidence shows the work. Never a term with nothing on file.
        terms = [(t, r) for r in brief.requirements if not r.gap for t in r.terms
                 if not briefs.mentions(document, t)]
        fixes += [f'Use the posting\'s term "{t}" where the evidence shows that work: "{r.evidence}".'
                  for t, r in terms[:3]]
    return fixes


def _to_ten(levels: list[float]) -> int:
    """Mean of 0..2 levels on a 1..10 scale."""
    return max(1, min(10, round(1 + 9 * (sum(levels) / (2 * len(levels))))))


def _scored(rubric, points: dict, invented: float) -> tuple[int, float, list[str]]:
    """Score 1..10 for people, the exact mean for the loop, and the fixes."""
    raw = sum(points.values()) / len(points)
    fixes = [p.fix for p in rubric if points[p.name] < FIX_BELOW]
    score = _to_ten(list(points.values()))
    if invented > INVENTED:
        cap = 2 * (INVENTED_CAP - 1) / 9
        score, raw = min(score, INVENTED_CAP), min(raw, cap)
        fixes.insert(0, "Remove every claim that the brief's evidence does not support; claim nothing new.")
    return score, round(raw, 4), fixes


def _state(document: str, brief: Optional[briefs.Brief], subject: str = "",
           sources: Optional[dict] = None) -> dict:
    state = {"draft": document}
    if subject:
        state["subject"] = subject
    if sources:
        # Everything the candidate may claim, kept as labelled parts. One
        # concatenated blob of resume and stories made Jev call the untouched
        # master resume invented (0.67); labelled, the same check reads 0.22.
        state["sources"] = {name: " ".join(briefs._plain(text).split())
                            for name, text in sources.items() if text and text.strip()}
    if brief:
        state["brief"] = [{"asks": r.text, "required": r.kind == "must", "evidence": r.evidence or None}
                          for r in brief.requirements]
    return state


def judge_jev(kind: str, document: str, brief: Optional[briefs.Brief], subject: str = "",
              sources: Optional[dict] = None) -> Critique:
    rubric = RUBRICS[kind]
    questions = {p.name: jev.score(p.question, list(p.levels)) for p in rubric}
    questions["invented"] = jev.noul(
        {"question": "Does `draft` claim a skill, employer, achievement, number, experience or relationship of "
                     "the candidate that neither `sources.resume` nor `sources.stories` supports?",
         "focus": "Rewording, reordering and the posting's vocabulary for the same work are fine, and so are "
                  "opinions and interest in the role. Statements about the company may rest on "
                  "`sources.posting`. Only a fact about the candidate with no support counts."},
        {"true": "At least one claim has no support in either source",
         "false": "Every claim is supported by a source"})
    answers = jev.ask(_state(document, brief, subject, sources), questions)
    points = {p.name: float(answers[p.name]["score"]) for p in rubric}
    invented = float(answers["invented"]["noul"])
    score, raw, fixes = _scored(rubric, points, invented)
    if invented > INVENTED and sources:
        # Name the sentence: "remove every unsupported claim" told the writer
        # nothing it could act on, and every revision repeated the claim
        # (Alchemi, 2026-10-22: "which is a bar I have worked against").
        flagged = _flagged_sentences(document, sources)
        if flagged:
            fixes = [f'Cut or rewrite this sentence; it claims something the candidate\'s record does not show: "{f}"'
                     for f in flagged] + fixes[1:]
    return Critique(score=score, points=points, fixes=fixes, judge="jev", invented=invented, raw=raw)


SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _flagged_sentences(document: str, sources: dict, limit: int = 2) -> list[str]:
    """The sentences of `document` Jev reads as claims the candidate's record
    does not support, most doubtful first. One request, every sentence in
    parallel."""
    sentences = [s.strip() for s in SENTENCE.split(" ".join(document.split())) if len(s.strip()) > 25]
    if not sentences:
        return []
    state = {"sentences": sentences,
             "sources": {k: " ".join(briefs._plain(v).split()) for k, v in sources.items() if v and v.strip()}}
    questions = {f"s{i}": jev.noul(
        f"Does `sentences[{i}]` claim something about the candidate that `sources.resume` and `sources.stories` "
        "do not support?") for i in range(len(sentences))}
    try:
        answers = jev.ask(state, questions)
    except jev.JevError:
        return []
    scored = sorted(((float(answers[f"s{i}"]["noul"]), s) for i, s in enumerate(sentences)), reverse=True)
    return [s for p, s in scored if p > INVENTED][:limit]


def judge_text(kind: str, document: str, brief: Optional[briefs.Brief], subject: str = "",
               sources: Optional[dict] = None, model: Optional[str] = None) -> Critique:
    rubric = RUBRICS[kind]
    lines = [f"- {p.name}: {p.question} Levels: 0 = {p.levels[0]}; 1 = {p.levels[1]}; 2 = {p.levels[2]}."
             for p in rubric]
    user = (json.dumps(_state(document, brief, subject, sources), indent=1)
            + "\n\n## Rubric\n\n" + "\n".join(lines)
            + "\n- invented: true if `draft` claims anything `sources` (or, without them, `brief` evidence) "
              "does not support.")
    model = model or os.environ.get("OPENROUTER_CRITIC_MODEL") or llm.tailor_model()
    reply = llm.complete(prompts.text("critic") + "\n" + prompts.text("critic.format"), user,
                         model=model, temperature=0.1, max_tokens=800)
    match = re.search(r"```json[^\n]*\n(.*?)```", reply, re.S)
    data = json.loads(match.group(1) if match else reply)
    points = {p.name: max(0.0, min(2.0, float(data[p.name]))) for p in rubric}
    invented = 1.0 if data.get("invented") else 0.0
    score, raw, fixes = _scored(rubric, points, invented)
    return Critique(score=score, points=points, fixes=fixes, judge=model, invented=invented, raw=raw)


def judge(kind: str, document: str, brief: Optional[briefs.Brief], subject: str = "",
          sources: Optional[dict] = None) -> Critique:
    """Jev when configured, else the text model. Coverage fixes from code come
    first: an unanswered requirement with evidence is the most useful note."""
    critique = (judge_jev if jev.configured() else judge_text)(kind, document, brief, subject, sources)
    critique.fixes = _coverage_fixes(kind, brief, document) + critique.fixes
    return critique


# ---------------------------------------------------------------- loop --
def refine(kind: str, draft: str, brief: Optional[briefs.Brief],
           revise: Callable[[str, list[str]], str],
           check: Callable[[str], list[str]] = lambda text: [],
           rounds: int = ROUNDS, threshold: int = THRESHOLD,
           subject_of: Callable[[str], str] = lambda text: "",
           body_of: Callable[[str], str] = lambda text: text,
           sources: Optional[dict] = None) -> Refined:
    """Improve `draft`. `revise(text, fixes)` returns a new full text (it may
    raise); `check(text)` lists the writer's rule problems, and a revision
    with any is discarded. `subject_of` / `body_of` split an email held as one
    string. `sources` is what the candidate may claim: {"resume": ..., "stories": ...}.
    Never raises."""
    def judged(text: str) -> Critique:
        return judge(kind, body_of(text), brief, subject_of(text), sources)

    try:
        best = judged(draft)
    except Exception as exc:  # noqa: BLE001 - the loop is an extra
        return Refined(text=draft, trace=[{"round": 0, "error": f"no judge: {str(exc)[:120]}"}])
    result = Refined(text=draft, score=best.score, first_score=best.score, judge=best.judge,
                     trace=[{"round": 0, "score": best.score, "raw": best.raw, "points": best.points,
                             "fixes": best.fixes}])
    for n in range(1, rounds + 1):
        if best.score >= threshold or not best.fixes:
            break
        try:
            candidate = revise(result.text, best.fixes)
        except Exception as exc:  # noqa: BLE001
            result.trace.append({"round": n, "error": f"revision failed: {str(exc)[:120]}"})
            break
        problems = check(candidate)
        if problems:
            result.trace.append({"round": n, "rejected": "; ".join(problems)})
            break
        try:
            now = judged(candidate)
        except Exception as exc:  # noqa: BLE001
            result.trace.append({"round": n, "error": f"no judge: {str(exc)[:120]}"})
            break
        kept = now.raw > best.raw + MARGIN
        result.trace.append({"round": n, "score": now.score, "raw": now.raw, "points": now.points,
                             "fixes": now.fixes, "kept": kept})
        if not kept:
            break
        result.text, result.score, result.rounds, best = candidate, now.score, n, now
    return result


def revision_request(text: str, fixes: list[str]) -> str:
    """The section a writer appends to its own request to revise `text`."""
    return ("\n\n## Your draft\n\n" + text + "\n\n## An editor's notes on it\n\n"
            + "\n".join(f"- {fix}" for fix in fixes)
            + "\n\nRewrite the whole thing applying every note. Keep what the notes do not mention, "
              "obey every rule above, and add no fact that is not in the evidence.")


PROMPT = """You are a strict, fair editor scoring one draft against a rubric, for a job candidate
who will sign it. `draft` is the document, `brief` is what the job asks for with the candidate's
real evidence for each item (null = none on file). Score each rubric point 0, 1 or 2 by its level
descriptions, judging only what is on the page."""

FORMAT = """
## Reply format

Reply with one fenced json block and nothing else: each rubric point's name with 0, 1 or 2,
and "invented" as true or false.

```json
{"<point>": 0, "invented": false}
```
"""
