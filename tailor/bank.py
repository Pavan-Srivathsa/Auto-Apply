"""The bullet bank: resume bullets drafted for jobs from the knowledge base,
kept when they are good, and reused instead of drafted again.

For each job on the resume (Koantek, BlackBuckInsights...), bullets are
drafted for the posting at hand from that job's interview story and its
current bullets. The master resume is never changed: drafts are options
the tailoring may put in place of a weaker bullet under the same job,
within the same printed lines.

A draft is kept in the bank only when it is good: every figure and name in
it is on file (the story or the master resume; never the posting, since a
bank bullet outlives the posting it was written for), and Jev reads it as
supported by the sources and strong (result first, a real measure). The
bank is the cache: before drafting for a job, the bullets already banked
for that employer that speak to this posting's terms are pulled, and new
ones are drafted only when there are not enough. The person can read the
bank and delete what they do not want (`server/prompts.py` neighbours,
`/bullets`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paths

from . import brief as briefs
from . import jev, llm, prompts
from . import tailor as tailoring

ENOUGH = 3            # banked bullets for one job that speak to the posting: no new drafts
DRAFTS = 4            # bullets drafted for one job when the bank is short
SUPPORTED = 0.7       # Jev: probability every claim is in the sources
STRONG = 1.4          # Jev: 0..2 score for result-first, measured, specific
_LOCK = threading.Lock()


def bank_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_BULLET_BANK") or paths.DATA / "bullet_bank.json")


@dataclass
class Bullet:
    company: str
    role: str
    text: str                                   # plain text; the tailoring writes the LaTeX
    terms: list[str] = field(default_factory=list)     # posting terms it uses
    covers: list[str] = field(default_factory=list)    # requirements it was written for
    sources: list[str] = field(default_factory=list)   # story slugs, "resume"
    supported: Optional[float] = None
    rating: Optional[float] = None
    created: str = ""
    jobs: list[str] = field(default_factory=list)      # postings it was drafted or reused for
    uses: int = 0

    @property
    def id(self) -> str:
        return hashlib.sha256(f"{self.company.lower()}|{briefs.loose(self.text)}".encode()).hexdigest()[:10]

    def to_dict(self) -> dict:
        return asdict(self) | {"id": self.id}


# --------------------------------------------------------------- storage --
def load() -> list[Bullet]:
    try:
        rows = json.loads(bank_path().read_text()).get("bullets", [])
    except (OSError, ValueError):
        return []
    fields = set(Bullet.__dataclass_fields__)
    return [Bullet(**{k: v for k, v in row.items() if k in fields}) for row in rows if isinstance(row, dict)]


def _write(bullets: list[Bullet]) -> None:
    path = bank_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as handle:
        json.dump({"bullets": [b.to_dict() for b in bullets]}, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def keep(new: list[Bullet], job: str = "") -> int:
    """Add good bullets; one already there (same employer, same words) is
    marked used again instead. Returns how many were new."""
    with _LOCK:
        bank = load()
        by_id = {b.id: b for b in bank}
        added = 0
        for bullet in new:
            if bullet.id in by_id:
                old = by_id[bullet.id]
                if job and job not in old.jobs:
                    old.jobs.append(job)
                continue
            bank.append(bullet)
            by_id[bullet.id] = bullet
            added += 1
        _write(bank)
    return added


def mark_used(ids: list[str], job: str) -> None:
    with _LOCK:
        bank = load()
        for bullet in bank:
            if bullet.id in ids:
                bullet.uses += 1
                if job and job not in bullet.jobs:
                    bullet.jobs.append(job)
        _write(bank)


def delete(bullet_id: str) -> bool:
    with _LOCK:
        bank = load()
        kept = [b for b in bank if b.id != bullet_id]
        _write(kept)
    return len(kept) != len(bank)


# ------------------------------------------------------- jobs and stories --
def jobs_on(master: str) -> list[dict]:
    """The jobs under EXPERIENCE: title, company, and their current bullets."""
    body = tailoring.split_sections(master).get("EXPERIENCE", "")
    heads = list(tailoring.ROLE_HEADING.finditer(body))
    out = []
    for i, head in enumerate(heads):
        segment = body[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(body)]
        out.append({"title": " ".join(head.group(1).split()), "company": " ".join(head.group(2).split()),
                    "bullets": [" ".join(tailoring.layout.visible(b).split()) for b in tailoring.bullets(segment)]})
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def story_for(company: str, stories_dir: Optional[Path] = None) -> tuple[str, str]:
    """(slug, tailor.md) of the interview story about this employer, else ("", "")."""
    from . import profile
    root = stories_dir or profile.STORIES
    key = _slug(company)[:12]
    if not key or not Path(root).exists():
        return "", ""
    for folder in sorted(Path(root).iterdir()):
        if folder.is_dir() and not folder.name.startswith("_") and key in _slug(folder.name):
            doc = folder / "tailor.md"
            if doc.exists():
                return folder.name, doc.read_text(errors="replace")
    return "", ""


# ------------------------------------------------------------- grounding --
def ungrounded(text: str, sources: str) -> list[str]:
    """Figures and names in `text` that are not on file in `sources`."""
    figures = sorted(tailoring._numbers(text) - tailoring._numbers(sources))
    known = tailoring._words(sources)
    names = sorted(word for stem, word in tailoring._named(text).items() if not tailoring._is_known(stem, known))
    return figures + names


def judge(texts: list[str], sources: str) -> list[tuple[Optional[float], Optional[float]]]:
    """(supported, strength) per bullet from Jev, or (None, None) without a key."""
    if not texts or not jev.configured():
        return [(None, None)] * len(texts)
    questions = {}
    for i in range(len(texts)):
        questions[f"s{i}"] = jev.noul(
            {"question": f"Is every claim in `bullets[{i}]` supported by `sources`?",
             "focus": "Rewording is fine; a fact, number, tool or scope the sources do not show is not."})
        questions[f"q{i}"] = jev.score(
            f"How strong is `bullets[{i}]` as a resume bullet?",
            ["Weak: a duty or a vague claim", "Fair: specific but no result or measure",
             "Strong: leads with a result, carries a real measure, names the work concretely"])
    answers = jev.ask({"bullets": texts, "sources": " ".join(sources.split())}, questions)
    return [(round(float(answers[f"s{i}"]["noul"]), 3), round(float(answers[f"q{i}"]["score"]), 3))
            for i in range(len(texts))]


# ------------------------------------------------------------- drafting --
def relevant(bank: list[Bullet], company: str, brief: briefs.Brief) -> list[Bullet]:
    """Banked bullets for this employer that use at least one of this
    posting's terms, most terms first."""
    terms = [t for r in brief.requirements for t in r.terms]
    scored = []
    for bullet in bank:
        if _slug(bullet.company) != _slug(company):
            continue
        hits = sum(1 for t in terms if briefs.mentions(bullet.text, t))
        if hits:
            scored.append((hits, bullet))
    return [b for _, b in sorted(scored, key=lambda row: -row[0])]


def draft(job: dict, story: str, brief: briefs.Brief, model: Optional[str] = None) -> list[dict]:
    """Up to DRAFTS bullets for one job, for this posting. Raises on a model failure."""
    longest = max((len(b) for b in job["bullets"]), default=180)
    user = (brief.as_message()
            + f"\n\n## The job\n\n{job['title']} at {job['company']}"
            + "\n\n## Its current resume bullets (facts you may use)\n\n" + "\n".join(f"- {b}" for b in job["bullets"])
            + f"\n\n## Its interview story (facts you may use)\n\n{story or '(none)'}"
            + f"\n\nWrite at most {DRAFTS} bullets, each at most {longest} characters.")
    reply = llm.complete(prompts.text("bank.draft") + "\n" + prompts.text("bank.format"), user,
                         model=model or llm.tailor_model(), temperature=0.3, max_tokens=1500)
    match = re.search(r"```json[^\n]*\n(.*?)```", reply, re.S)
    data = json.loads(match.group(1) if match else reply)
    return [row for row in data.get("bullets", []) if isinstance(row, dict) and str(row.get("text", "")).strip()]


def for_job(brief: briefs.Brief, master: str, job_key: str = "", model: Optional[str] = None) -> dict:
    """Bullets for every job on the resume, for this posting: banked ones
    first, new drafts only where the bank is short. Good new drafts go into
    the bank. Returns what the job folder keeps (`bullets.json`) and what the
    tailoring is offered."""
    bank = load()
    result = {"jobs": [], "stories": []}
    for job in jobs_on(master):
        slug, story = story_for(job["company"])
        reused = relevant(bank, job["company"], brief)[:DRAFTS]
        row = {"company": job["company"], "role": job["title"], "story": slug,
               "reused": [b.to_dict() for b in reused], "drafted": [], "rejected": []}
        if slug:
            result["stories"].append(slug)
        if len(reused) < ENOUGH and (story or job["bullets"]):
            sources = story + "\n" + "\n".join(job["bullets"])
            try:
                drafts = draft(job, story, brief, model)
            except Exception as exc:  # noqa: BLE001 - the bank is an extra
                row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                drafts = []
            texts, kept_rows = [], []
            for d in drafts:
                text = " ".join(str(d["text"]).split())
                problems = ungrounded(text, sources)
                if problems:
                    row["rejected"].append({"text": text, "why": "not on file: " + ", ".join(problems)})
                    continue
                texts.append(text)
                kept_rows.append(d)
            try:
                ratings = judge(texts, sources)
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"judge: {str(exc)[:160]}"
                ratings = [(None, None)] * len(texts)
            good = []
            for text, d, (supported, strength) in zip(texts, kept_rows, ratings):
                bullet = Bullet(company=job["company"], role=job["title"], text=text,
                                terms=[str(t) for t in d.get("terms", [])][:6],
                                covers=[str(c) for c in d.get("covers", [])][:4],
                                sources=[s for s in (slug, "resume") if s], supported=supported, rating=strength,
                                created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                jobs=[job_key] if job_key else [])
                verdict = (supported is not None and supported >= SUPPORTED and strength is not None and strength >= STRONG)
                (good if verdict else row["rejected"]).append(
                    bullet if verdict else {"text": text, "why": f"Jev: supported {supported}, strength {strength}"})
                row["drafted"].append(bullet.to_dict() | {"banked": verdict})
            keep(good, job_key)
        mark_used([b["id"] for b in row["reused"]], job_key)
        result["jobs"].append(row)
    return result


def options(result: dict) -> str:
    """The tailoring's section: per job, the bullets it may put in place of
    a weaker one under the same job. Banked and freshly approved only."""
    blocks = []
    for job in result.get("jobs", []):
        rows = [b["text"] for b in job["reused"]] + [b["text"] for b in job["drafted"] if b.get("banked")]
        if rows:
            blocks.append(f"### {job['role']} at {job['company']}\n" + "\n".join(f"- {t}" for t in rows))
    if not blocks:
        return ""
    return ("## Checked bullets for this posting, from the candidate's knowledge base\n\n"
            "Each is on file and was judged strong. Under the same job you may put one in place of a "
            "weaker bullet when it answers the posting better, keeping its facts as written; it still "
            "has to print on that bullet's lines. Escape % as \\% in LaTeX.\n\n" + "\n\n".join(blocks))


DRAFT_PROMPT = """You write resume bullets for one job the candidate held, aimed at one job posting.

- Use only facts in the job's current bullets and its interview story. Every number, tool,
  product and scope must be there. Never take a fact from the posting; you may use the
  posting's word for work the candidate truly did ("feature pipelines" for their pipeline).
- Each bullet answers one requirement from the brief that this job's facts really answer.
  If none does, write fewer bullets, or none.
- Shape: a past-tense verb, the result first, then how; a real measure from the facts; one
  idea; no "Responsible for"; no first person; plain text, no LaTeX.
"""

DRAFT_FORMAT = """
## Reply format

Reply with one fenced json block and nothing else:

```json
{"bullets": [{"text": "<the bullet>", "covers": ["<requirement it answers>"], "terms": ["<posting term it uses>"]}]}
```
"""
