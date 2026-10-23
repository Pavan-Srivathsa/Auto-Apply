"""The interview story bank, built on the profile interview's own files.

The source of truth stays where it is: `base/stories/<slug>/` holds the
interview's `main.md` (the story in the candidate's words, written from the
transcript), `star.md` (its STAR document) and `tailor.md` (stack and
figures, which tailoring already reads). This module reads those, never
rewrites them, and adds two things on top in `data/career/stories.json`:

- **verification**: a story is a draft until the person confirms it; only a
  confirmed story is offered as "verified" for interview prep.
- **provenance**: every figure in the STAR text is checked against the
  candidate's own words (`main.md`) and the master resume; one found in
  neither is listed as unconfirmed, and interview prep shows it as such.

Suggestions for roles on the resume with no story yet are drafts made of the
resume's own bullets, with the situation, task and lessons left for the
person: nothing is invented. Interview prep matches a posting's requirements
to stories (Jev when configured, word overlap otherwise) and asks a model for
practice questions only, never answers. The figure check follows Career-Ops'
story-provenance-check.mjs (MIT), reimplemented.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paths

CATEGORIES = {
    "technical_challenge": r"\bchalleng|\bhard\b|\bdifficult|\bproblem\b",
    "system_optimization": r"\blatency|\bthroughput|\boptimi[sz]|\bfaster|\bcach(e|ing)\b|\bmemory\b|\bp9[59]\b",
    "architecture_decision": r"\barchitect|\bdesign(ed)?\b|\bchose\b|\btrade-?off|\binstead of\b",
    "debugging_incident": r"\bdebug|\bincident|\broot[- ]cause|\boutage|\bfailure|\bbug\b",
    "production_reliability": r"\bproduction\b|\breliab|\bmonitor|\btelemetry|\bretry|\bself-correct",
    "model_deployment": r"\bdeploy|\bserv(e|ing)\b|\bvllm|\bkubernetes|\bdocker|\bquantiz",
    "ml_experiment": r"\bbenchmark|\bevaluat|\bholdout|\bf1\b|\bauc\b|\bmape\b|\bexperiment|\ba/b\b|\bablation",
    "collaboration": r"\bteam\b|\bstakeholder|\bcollaborat|\bclient\b|\bpartner",
    "leadership": r"\bled\b|\blead(ing)?\b|\bmentor|\bowner\b|\bfour engineers|\bmanag",
    "failure_and_learning": r"\bfail(ed)?\b|\bmistake|\blearn(ed|t)\b|\bwrong\b",
    "deadline_tradeoff": r"\bdeadline|\btime pressure|\bweeks?\b.*\bship|\bscope\b",
    "ambiguous_requirements": r"\bambigu|\bunclear|\bfigur(e|ing) out\b|\bwhich signals",
    "customer_impact": r"\bcustomer|\busers?\b|\bbusiness\b|\bsavings|\brevenue|\bfinance\b",
}
QUESTIONS = {
    "technical_challenge": "Tell me about the hardest technical problem you solved.",
    "system_optimization": "Walk me through a performance optimization you made and how you measured it.",
    "architecture_decision": "Describe an architecture decision and the trade-offs you weighed.",
    "debugging_incident": "Tell me about a production issue you debugged.",
    "production_reliability": "How did you make a system reliable in production?",
    "model_deployment": "How did you take a model from experiment to serving?",
    "ml_experiment": "Describe an experiment or benchmark you designed and what it showed.",
    "collaboration": "Tell me about working with a difficult stakeholder or another team.",
    "leadership": "Tell me about a time you led a project or other engineers.",
    "failure_and_learning": "Tell me about something that went wrong and what you learned.",
    "deadline_tradeoff": "Tell me about a deadline that forced a trade-off.",
    "ambiguous_requirements": "Tell me about a project that started with unclear requirements.",
    "customer_impact": "What impact did your work have on users or the business?",
}
_lock = threading.Lock()


@dataclass
class Story:
    slug: str
    title: str
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    lessons: str = ""
    technologies: list = field(default_factory=list)
    figures_confirmed: list = field(default_factory=list)
    figures_unconfirmed: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    completeness: float = 0.0
    status: str = "draft"            # draft | verified
    source: str = ""                 # where it came from: "interview" | "resume bullet"
    evidence: list = field(default_factory=list)   # files and lines it rests on


def _stories_dir() -> Path:
    from tailor import profile
    return Path(profile.STORIES)


def _state_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_STORY_STATE") or paths.DATA / "career" / "stories.json")


def _state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def confirm(slug: str, verified: bool = True) -> dict:
    with _lock:
        data = _state()
        data[slug] = {"verified": verified, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        target = _state_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(target)
    return data[slug]


def _sections(markdown: str) -> dict[str, str]:
    """Situation / Task / Action / Result / Lessons by heading, any level."""
    out: dict[str, list[str]] = {}
    key = None
    for line in markdown.splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            name = heading.group(1).lower()
            key = next((k for k in ("situation", "task", "action", "result", "lesson") if k in name), None)
            key = key or ("lesson" if "reflection" in name else None)
            continue
        if key:
            out.setdefault(key, []).append(line)
    # The interview writes "Not discussed." where the conversation never went.
    joined = {k: " ".join(" ".join(v).split()) for k, v in out.items()}
    return {k: v for k, v in joined.items() if not re.fullmatch(r"(?i)not discussed\.?", v.strip())}


def _numbers(text: str) -> set[str]:
    from tailor import tailor as tailoring
    return tailoring._numbers(text)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _master() -> str:
    from tailor import layout
    try:
        return layout.visible((paths.BASE / "resume.tex").read_text())
    except OSError:
        return ""


def _classify(text: str) -> list[str]:
    low = text.lower()
    return [name for name, pattern in CATEGORIES.items() if re.search(pattern, low)]


def bank() -> list[Story]:
    """Every story the interview wrote, with its provenance and status."""
    root, state, master = _stories_dir(), _state(), _master()
    out = []
    if not root.exists():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        star, main, tailor_md = _read(folder / "star.md"), _read(folder / "main.md"), _read(folder / "tailor.md")
        if not (star or main):
            continue
        parts = _sections(star or main)
        title = next((l.lstrip("# ").strip() for l in (star or main).splitlines() if l.startswith("#")), folder.name)
        title = re.sub(r"^(star|story) document\s*[—-]\s*", "", title, flags=re.I)
        stack = re.search(r"^Stack:\s*(.+)$", tailor_md, re.M)
        body = " ".join(parts.get(k, "") for k in ("situation", "task", "action", "result"))
        own_words = main + "\n" + master
        figures = sorted(_numbers(body))
        confirmed = [f for f in figures if f in _numbers(own_words)]
        filled = sum(1 for k in ("situation", "task", "action", "result") if len(parts.get(k, "")) > 40)
        categories = _classify(body)
        story = Story(
            slug=folder.name, title=title, situation=parts.get("situation", ""), task=parts.get("task", ""),
            action=parts.get("action", ""), result=parts.get("result", ""), lessons=parts.get("lesson", ""),
            technologies=[t.strip() for t in stack.group(1).split(",")][:25] if stack else [],
            figures_confirmed=confirmed, figures_unconfirmed=[f for f in figures if f not in confirmed],
            categories=categories, questions=[QUESTIONS[c] for c in categories],
            completeness=round((filled / 4) * (1.0 if not figures else len(confirmed) / len(figures)), 2),
            status="verified" if state.get(folder.name, {}).get("verified") else "draft",
            source="interview", evidence=[str(p.name) for p in (folder / "star.md", folder / "main.md") if p.exists()])
        out.append(story)
    return out


def suggestions() -> list[Story]:
    """Drafts for roles on the resume with no story: the resume's own bullets
    as the result, everything else left for the person."""
    from tailor import bank as bullets
    from tailor import layout
    try:
        jobs = bullets.jobs_on((paths.BASE / "resume.tex").read_text())
    except OSError:
        return []
    have = {re.sub(r"[^a-z0-9]", "", s.slug) for s in bank()}
    out = []
    for job in jobs:
        key = re.sub(r"[^a-z0-9]", "", job["company"].lower())[:10]
        if not key or any(key in slug for slug in have):
            continue
        result = " ".join(job["bullets"])
        out.append(Story(slug=f"suggested-{key}", title=f"{job['title']}, {job['company']}", result=result,
                         categories=_classify(result), questions=[QUESTIONS[c] for c in _classify(result)],
                         figures_confirmed=sorted(_numbers(result)), status="draft", source="resume bullet",
                         completeness=0.25, evidence=["the master resume"]))
    return out


# --------------------------------------------------------- interview prep --
PREP_PROMPT = """You prepare a candidate for interviews for one specific role.
Write practice questions only: the ones this role's interviewers are likely to ask,
grounded in the posting's actual responsibilities and stack. Mix behavioural, technical
deep-dive and system-design questions. Never write answers, never describe the candidate,
never invent facts about the company beyond the posting.

Reply with one fenced block and nothing else:

```json
{"questions": [{"q": "<question>", "kind": "behavioral" | "technical" | "system_design", "from": "<the posting phrase it comes from>"}]}
```
"""


def _prep_path(job_id: str) -> Path:
    return paths.DATA / "career" / "prep" / f"{job_id}.json"


def _score(requirement: str, story: Story) -> float:
    from tailor import tailor as tailoring
    text = " ".join([story.title, story.action, story.result, " ".join(story.technologies)])
    words = tailoring._words(requirement)
    return len(words & tailoring._words(text)) / max(1, len(words))


def prep(job_id: str, refresh: bool = False) -> dict:
    """Responsibilities from the brief, the stories that answer each, the
    ones nothing answers, and practice questions. Cached per job."""
    from server import queue
    from tailor import brief as briefs
    from tailor import jev, llm, prompts
    cached = _prep_path(job_id)
    if cached.exists() and not refresh:
        return json.loads(cached.read_text())
    job = queue.get(job_id)
    if job is None or not job.app_dir:
        raise FileNotFoundError("no application folder for this job")
    app_dir = Path(job.app_dir)
    brief = briefs.load(app_dir)
    requirements = [r.text for r in brief.requirements] if brief else []
    stories = bank()
    matches = []
    judged = None
    if requirements and stories and jev.configured():
        try:
            state = {"requirements": requirements, "stories": [
                {"title": s.title, "work": " ".join([s.action, s.result])[:1500]} for s in stories]}
            questions = {f"r{i}s{j}": jev.score(
                f"How well does `stories[{j}]` show what `requirements[{i}]` asks for?",
                ["Not at all", "Related work", "Directly shows it"])
                for i in range(len(requirements)) for j in range(len(stories))}
            answers = jev.ask(state, questions)
            judged = {k: float(v["score"]) / 2 for k, v in answers.items()}
        except Exception:  # noqa: BLE001 - word overlap is the fallback, said as such
            judged = None
    for i, req in enumerate(requirements):
        ranked = sorted(((judged[f"r{i}s{j}"] if judged else _score(req, s), s) for j, s in enumerate(stories)),
                        key=lambda pair: -pair[0])
        best = [{"slug": s.slug, "title": s.title, "score": round(score, 2), "status": s.status,
                 "unconfirmed_figures": s.figures_unconfirmed}
                for score, s in ranked[:2] if score >= (0.5 if judged else 0.25)]
        matches.append({"requirement": req, "stories": best})
    posting = _read(app_dir / "posting.md")[:9000]
    practice, error = [], ""
    if posting:
        try:
            reply = llm.complete(prompts.text("career.prep"), f"## Posting\n\n{posting}\n\n## Role\n\n{job.title} at {job.company}",
                                 temperature=0.3, max_tokens=1500)
            found = re.search(r"```json[^\n]*\n(.*?)```", reply, re.S)
            practice = json.loads(found.group(1)).get("questions", [])[:15] if found else []
        except Exception as exc:  # noqa: BLE001 - the matches stand without questions
            error = f"practice questions unavailable: {str(exc)[:120]}"
    out = {"job_id": job_id, "title": job.title, "company": job.company, "matched_by": "jev" if judged else "word overlap",
           "matches": matches, "missing": [m["requirement"] for m in matches if not m["stories"]],
           "practice": practice, "error": error, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(out, indent=1))
    return out


def to_dict(story: Story) -> dict:
    return asdict(story)
