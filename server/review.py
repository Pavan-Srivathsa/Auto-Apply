"""Checkpoint 1: the review API.

Serves each application's artifacts to the review page and records the human
decision. Approval is the only path forward in the pipeline — the browser fill
loop refuses to touch a job that is not APPROVED — so this module is the gate,
not a formality.

Revision is a second tailoring pass with the reviewer's instruction appended to
the prompt. The result lands in a fresh application folder, because nothing in
an existing one is ever overwritten.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

import paths
from archive import store
from browser import chrome, guard
from server import corrections, queue, runner, seen, thread as job_thread
from server.models import REJECT_LABELS, Job, RejectReason, Status
from tailor import answers, cover, fetch, llm, profile, resume_bases, tailor
from tailor import brief as briefs
from tailor import qualify

router = APIRouter(prefix="/review", tags=["review"])

# Where apply.py writes its per-job logs.
DATA_DIR = paths.DATA

# Artifacts the review page may request. An allow-list rather than a path join,
# so a crafted name cannot walk out of the application folder.
ARTIFACTS = {
    "posting.md": "text/markdown",
    "resume.tex": "text/plain",
    "resume.diff": "text/plain",
    "suggestions.md": "text/markdown",
    "mismatch.md": "text/markdown",
    "screen.json": "application/json",
    "rejection.md": "text/markdown",
    "fill_notes.md": "text/markdown",
    "answers.md": "text/markdown",
    "error.txt": "text/plain",
    "resume.pdf": "application/pdf",
    "resume_changes.pdf": "application/pdf",
    "resume_both.pdf": "application/pdf",
    "cover_letter.md": "text/markdown",
    "cover_letter.tex": "text/plain",
    "cover_letter.pdf": "application/pdf",
    "cover_letter_error.txt": "text/plain",
    "fill_screenshot.png": "image/png",
}


class Decision(BaseModel):
    note: Optional[str] = None


class FillRequest(BaseModel):
    """`force` is the second gate: the page asks the human before sending it."""

    note: Optional[str] = None
    force: bool = False
    mode: str = "jobright_check"


class Rejection(BaseModel):
    """A rejection always carries a reason. The note is for the specifics."""

    reason: RejectReason
    note: Optional[str] = None


class Question(BaseModel):
    question: str


class Revision(BaseModel):
    instruction: str


class ThreadMessage(BaseModel):
    """One message in a job's thread: a question to answer, or a change to
    make. `keep_status` is the re-tailor that leaves a filled or submitted
    job where it stands and only writes new documents."""

    text: str
    action: str = "ask"          # ask | change | resume | cover
    keep_status: bool = True
    # A change asked for on the expensive model. The choice sticks to the
    # row, so the job stays an Opus job for anything it is re-tailored with
    # later; it is the same decision the banner's button makes at capture.
    premium: bool = False


class CoverRewrite(BaseModel):
    selected: str
    instruction: str
    premium: bool = False


class CoverSave(BaseModel):
    expected: str
    text: str


class ResumeRewrite(BaseModel):
    selected: str
    instruction: str
    premium: bool = False


class ResumeSave(BaseModel):
    expected: str
    text: str


class ResumeBaseChoice(BaseModel):
    base: str


def _job_and_dir(job_id: str) -> tuple[Job, Path]:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    if not job.app_dir:
        raise HTTPException(409, "this job has not been through the pipeline yet")
    app_dir = Path(job.app_dir)
    if not app_dir.exists():
        raise HTTPException(410, f"application folder {app_dir.name} is gone")
    return job, app_dir


@router.get("/{job_id}")
def detail(job_id: str) -> dict:
    """Everything the review page needs for one application."""
    job, app_dir = _job_and_dir(job_id)
    present = [name for name in ARTIFACTS if (app_dir / name).exists()]

    def read(name: str) -> str:
        path = app_dir / name
        return path.read_text(errors="replace") if path.exists() else ""

    base_choice = {}
    try:
        base_choice = json.loads(read("resume_base.json") or "{}")
    except json.JSONDecodeError:
        pass
    selected_id = str(base_choice.get("base") or job.resume_base or "")
    bases = resume_bases.all_bases(active_only=True)
    if selected_id and all(row.id != selected_id for row in bases):
        try:
            bases.append(resume_bases.get(selected_id))
        except KeyError:
            pass
    return {
        "id": job.id,
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "source": job.source,
        "status": job.status.value,
        "folder": app_dir.name,
        "artifacts": present,
        "diff": read("resume.diff"),
        "suggestions": read("suggestions.md"),
        "mismatch": read("mismatch.md"),
        # Are there documents to attach? Try again restarts a job that has a
        # compiled resume at the fill, and one without at the tailor.
        "tailored": _tailored(app_dir),
        # The ML/DS bullet review with the person's decisions (server/bullets.py);
        # None for a folder from before it existed.
        "bullet_review": _bullet_review(app_dir),
        "screen": _screen(app_dir),
        "screen_error": read("screen_error.txt"),
        "resume_source": read("resume.tex"),
        "resume_base": base_choice or {
            "base": job.resume_base,
            "source": job.resume_base_source,
            "confidence": job.resume_base_confidence,
            "reason": job.resume_base_reason,
        },
        "resume_bases": [
            {
                "id": row.id, "label": row.label,
                "description": row.description, "active": row.active,
            }
            for row in bases
        ],
        "cover_letter": read("cover_letter.md"),
        "cover_letter_error": read("cover_letter_error.txt"),
        "resume_models": {
            "default": job.tailor_model or llm.tailor_model(),
            "premium": llm.premium_model(),
        },
        "cover_models": {
            "default": job.tailor_model or llm.tailor_model(),
            "premium": llm.premium_model(),
        },
        "reject_reason": job.reject_reason.value if job.reject_reason else None,
        "reject_label": REJECT_LABELS.get(job.reject_reason) if job.reject_reason else None,
        "reject_note": job.reject_note,
        # Every fill appends its notes. The page shows the latest; the file
        # keeps the rest.
        "fill_notes": latest_section(read("fill_notes.md")),
        "answers": read("answers.md"),
        "questions": _open_questions(app_dir),
        "thread": job_thread.load(job.id),
        "thread_running": job_thread.running(job.id),
        "changes": corrections.changes(app_dir),
        "stories_used": [line for line in read("stories_used.txt").splitlines() if line.strip()],
        "error": read("error.txt"),
        "error_detail": job.error,
        "log": _log_path(job.id),
        "history": _history(app_dir),
        "fill": _fill_state(job.id),
        "fit": _fit(app_dir),
    }


def _ats(brief, resume: str, on_file: str = "") -> Optional[dict]:
    """The posting's own terms (the brief's spellings) found in the finished
    resume, the way an ATS keyword filter reads it. A missing term is "on
    file" only when that term itself is in the master resume or the stories:
    a requirement answered by Python does not put Go on file (2026-10-22)."""
    if not resume:
        return None
    seen, rows = set(), []
    for req in sorted(brief.requirements, key=lambda r: r.kind != "must"):
        for term in req.terms:
            key = briefs.loose(term)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append({"term": term, "present": briefs.mentions(resume, term),
                         "evidence": briefs.mentions(on_file, term)})
    present = sum(1 for r in rows if r["present"])
    return {"present": present, "total": len(rows),
            "missing_fixable": [r["term"] for r in rows if not r["present"] and r["evidence"]],
            "missing_no_evidence": [r["term"] for r in rows if not r["present"] and not r["evidence"]]}


def _readable(quote: str) -> str:
    """A resume line as it prints: LaTeX dashes and escapes as their characters."""
    quote = quote.replace("---", "\u2014").replace("--", "\u2013")
    return re.sub(r"\\([%&$#_])", r"\1", quote)


def _fit(app_dir: Path) -> Optional[dict]:
    """What the job asks for and what the candidate has, as the brief read it,
    with whether the finished resume and letter answer each requirement and
    the letter's score from the critic loop. None for a job tailored before
    the brief existed."""
    brief = briefs.load(app_dir)
    if brief is None:
        return None

    def read(name: str) -> str:
        path = app_dir / name
        return path.read_text(errors="replace") if path.exists() else ""

    resume, letter = read("resume.tex"), read("cover_letter.md")
    try:
        on_file = read("master_resume.tex") + "\n" + tailor.load_profile(stories=profile.read_used(app_dir))
    except Exception:  # noqa: BLE001 - the card still shows, with a narrower "on file"
        on_file = read("master_resume.tex")
    rows = [{"text": r.text, "kind": r.kind, "evidence": _readable(r.evidence), "where": r.where,
             "strength": r.strength, "gap": r.gap,
             "in_resume": briefs.covered(r, resume) if resume else None,
             "in_letter": briefs.covered(r, letter) if letter else None}
            for r in brief.requirements]
    assessment = qualify.load(app_dir)
    if assessment and len(assessment.get("items", [])) == len(rows):
        for row, item in zip(rows, assessment["items"]):
            row["level"], row["reason"] = item.get("level"), item.get("reason", "")
    answerable = [row for row in rows if row["kind"] == "must" and not row["gap"]]
    def scored(name: str) -> Optional[dict]:
        try:
            review = json.loads(read(name) or "{}")
        except ValueError:
            return None
        return {"score": review.get("score"), "first": review.get("first_score"),
                "judge": review.get("judge"), "rounds": review.get("rounds")} if review.get("score") else None

    return {
        "role": brief.role,
        "rows": rows,
        "musts": len(answerable),
        "musts_gap": sum(1 for row in rows if row["kind"] == "must" and row["gap"]),
        "in_resume": sum(1 for row in answerable if row["in_resume"]),
        "judged_by_jev": any(row["strength"] is not None for row in rows),
        "ats": _ats(brief, resume, on_file),
        "letter": scored("cover_review.json"),
        "resume": scored("resume_review.json"),
        "verdict": {"call": assessment["verdict"], "summary": assessment["summary"],
                    "blockers": assessment.get("blockers", [])} if assessment else None,
    }


def _open_questions(app_dir: Path) -> list[str]:
    """Free-form questions the filled form still has empty, off the latest
    look at it (the tab's own pings, else the fill's snapshot)."""
    state = corrections._read(app_dir / corrections.FORM_STATE).get("fields")
    if not state:
        state = corrections._read(app_dir / corrections.FILL_REPORT).get("after_agent")
    return answers.open_questions(state or {})


def _answer(app_dir: Path, question: str, history: str = "") -> answers.Answer:
    """One answer from everything this application has: the posting, the
    tailored resume, the cover letter, the profile with the same stories
    the tailor read, the applicant facts, and what the thread already
    said."""
    context = answers.Context.from_app_dir(
        app_dir, profile=tailor.load_profile(stories=profile.read_used(app_dir)),
        applicant=profile.applicant_facts() or "")
    context.thread = history
    try:
        result = answers.answer(question, context)
    except guard.ProtectedField:
        raise HTTPException(422, "That is a visa / work-authorisation question; it is yours to answer.")
    except answers.AnswerError as error:
        raise HTTPException(502, str(error))
    store.write_or_append(app_dir, "answers.md",
                          f"# Asked on the review page\n\n**{question}**\n\n{result.text}\n")
    return result


@router.post("/{job_id}/ask")
def ask(job_id: str, body: Question) -> dict:
    """One free-form answer, kept in answers.md with the ones the fill
    produced; typed into the form by the human."""
    _, app_dir = _job_and_dir(job_id)
    question = " ".join(body.question.split())
    if not question:
        raise HTTPException(400, "question is empty")
    result = _answer(app_dir, question)
    return {"id": job_id, "question": result.question, "answer": result.text, "model": result.model}


@router.get("/{job_id}/thread")
def read_thread(job_id: str) -> dict:
    """The conversation about this job. Polled while a change runs."""
    queue.get(job_id) or _job_and_dir(job_id)
    return {"id": job_id, "turns": job_thread.load(job_id), "running": job_thread.running(job_id)}


@router.post("/{job_id}/thread")
def say(job_id: str, body: ThreadMessage) -> dict:
    """A message in the job's thread: answered, or tailored into new
    documents. A change never approves anything and never starts a fill."""
    job, app_dir = _job_and_dir(job_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "the message is empty")
    if body.action not in ("ask", "change", "resume", "cover"):
        raise HTTPException(422, f"no such action {body.action!r}")

    if body.action in ("change", "resume", "cover"):
        # Lazy imports: pipeline imports the server package.
        from pipeline import regenerate, retailor

        if body.premium:
            model = llm.premium_model()
            if job.tailor_model != model:
                job = queue.update(job.id, tailor_model=model) or job
        target = "both" if body.action == "change" else body.action
        run = (
            (lambda: retailor(job, text, keep_status=body.keep_status).name)
            if target == "both"
            else (lambda: regenerate(job, text, target).name)
        )
        try:
            job_thread.start_change(job_id, text, run, target=target)
        except job_thread.ThreadError as exc:
            raise HTTPException(409, str(exc))
        return {"id": job_id, "turns": job_thread.load(job_id), "running": True}

    question = " ".join(text.split())
    history = job_thread.history(job_id)
    job_thread.add(job_id, "question", question)
    try:
        result = _answer(app_dir, question, history)
    except HTTPException as exc:
        job_thread.add(job_id, "error", str(exc.detail))
        raise
    job_thread.add(job_id, "answer", result.text, model=result.model)
    return {"id": job_id, "turns": job_thread.load(job_id), "running": False}


@router.post("/{job_id}/cover/rewrite")
def rewrite_cover_selection(job_id: str, body: CoverRewrite) -> dict:
    """Rewrite highlighted cover-letter prose without saving it yet."""
    job, app_dir = _job_and_dir(job_id)
    letter_path = app_dir / "cover_letter.md"
    resume_path = app_dir / "resume.tex"
    if not letter_path.exists() or not resume_path.exists():
        raise HTTPException(409, "this job does not have both a cover letter and resume")
    letter = letter_path.read_text(errors="replace").strip()
    selected = body.selected.strip()
    if not selected or selected not in letter:
        raise HTTPException(409, "the highlighted text is stale; select it again")
    if not body.instruction.strip():
        raise HTTPException(400, "say how the highlighted text should change")

    context = answers.Context.from_app_dir(
        app_dir,
        profile=tailor.load_profile(stories=profile.read_used(app_dir)),
        applicant=profile.applicant_facts() or "",
    )
    posting = fetch.Posting(
        url=job.url, text=context.posting, title=job.title, company=job.company,
    )
    model = llm.premium_model() if body.premium else (job.tailor_model or llm.tailor_model())
    try:
        replacement = cover.rewrite_selection(
            posting, context.resume_tex, letter, selected, body.instruction,
            profile=context.profile, model=model,
        )
    except cover.CoverLetterError as exc:
        raise HTTPException(502, str(exc))
    return {"id": job_id, "replacement": replacement, "model": model}


@router.post("/{job_id}/resume/rewrite")
def rewrite_resume_selection(job_id: str, body: ResumeRewrite) -> dict:
    """Rewrite highlighted resume source without saving it yet."""
    job, app_dir = _job_and_dir(job_id)
    resume_path = app_dir / "resume.tex"
    if not resume_path.exists():
        raise HTTPException(409, "this job does not have an editable resume")
    resume_tex = resume_path.read_text(errors="replace")
    selected = body.selected
    if not selected.strip() or selected not in resume_tex:
        raise HTTPException(409, "the highlighted text is stale; select it again")
    if not body.instruction.strip():
        raise HTTPException(400, "say how the highlighted resume text should change")

    context = answers.Context.from_app_dir(
        app_dir,
        profile=tailor.load_profile(stories=profile.read_used(app_dir)),
        applicant=profile.applicant_facts() or "",
    )
    posting = fetch.Posting(
        url=job.url, text=context.posting, title=job.title, company=job.company,
    )
    model = llm.premium_model() if body.premium else (job.tailor_model or llm.tailor_model())
    try:
        replacement = tailor.rewrite_selection(
            posting, resume_tex, selected, body.instruction,
            profile=context.profile, model=model,
            master_tex=(app_dir / "master_resume.tex").read_text(errors="replace")
            if (app_dir / "master_resume.tex").exists() else None,
        )
    except tailor.TailorError as exc:
        raise HTTPException(502, str(exc))
    return {"id": job_id, "replacement": replacement, "model": model}


@router.post("/{job_id}/resume/save")
def save_resume_edit(job_id: str, body: ResumeSave) -> dict:
    """Validate, compile, and archive a reviewed resume source edit."""
    from pipeline import save_resume  # lazy: pipeline imports the server package
    from tex.compile import CompileError

    job, app_dir = _job_and_dir(job_id)
    current = (app_dir / "resume.tex").read_text(errors="replace") if (
        app_dir / "resume.tex"
    ).exists() else ""
    if body.expected != current:
        raise HTTPException(409, "the resume changed; reload before saving this edit")
    try:
        new_dir = save_resume(job, body.text)
    except (tailor.TailorError, CompileError) as exc:
        raise HTTPException(422, str(exc))
    return {
        "id": job_id,
        "folder": new_dir.name,
        "status": (queue.get(job_id) or job).status.value,
    }


@router.post("/{job_id}/resume-base")
def change_resume_base(job_id: str, body: ResumeBaseChoice) -> dict:
    """Select another master and rebuild both documents in a new version."""
    job, _ = _job_and_dir(job_id)
    try:
        base = resume_bases.get(body.base, active_only=True)
    except KeyError:
        raise HTTPException(404, f"no active resume called {body.base!r}") from None
    if base.id == job.resume_base:
        return {"id": job_id, "running": False, "base": base.id}

    from pipeline import retailor  # lazy: pipeline imports the server package

    instruction = f"Re-tailor from the manually selected master resume: {base.label}."

    def work() -> str:
        current = queue.update(
            job_id,
            resume_base=base.id,
            resume_base_source="manual",
            resume_base_confidence="high",
            resume_base_reason="Selected on the review page.",
        ) or job
        return retailor(current, instruction, keep_status=True).name

    try:
        job_thread.start_change(job_id, instruction, work, target="both")
    except job_thread.ThreadError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": job_id, "running": True, "base": base.id}


@router.post("/{job_id}/cover/save")
def save_cover_edit(job_id: str, body: CoverSave) -> dict:
    """Save the reviewed text into a new immutable application version."""
    from pipeline import save_cover  # lazy: pipeline imports the server package

    job, app_dir = _job_and_dir(job_id)
    current = (app_dir / "cover_letter.md").read_text(errors="replace") if (
        app_dir / "cover_letter.md"
    ).exists() else ""
    if body.expected != current:
        raise HTTPException(409, "the cover letter changed; reload before saving this edit")
    try:
        new_dir = save_cover(job, body.text)
    except cover.CoverLetterError as exc:
        raise HTTPException(422, str(exc))
    return {
        "id": job_id,
        "folder": new_dir.name,
        "status": (queue.get(job_id) or job).status.value,
    }


@router.post("/{job_id}/reveal")
def reveal(job_id: str) -> dict:
    """Open the application folder in Finder with the two named PDFs
    selected, for the forms whose questions never reach the assistant and
    whose files are quicker dragged in by hand."""
    import subprocess
    import sys

    import apply as apply_script  # lazy: apply.py pulls in the browser stack

    _, app_dir = _job_and_dir(job_id)
    # The named copies are made by the fill; a folder that was only
    # tailored has none yet, and the upload name is half the point of
    # dragging the file in by hand.
    files = [p for p in (apply_script.upload_copy(app_dir, "resume.pdf"),
                         apply_script.upload_copy(app_dir, "cover_letter.pdf",
                                                  apply_script.cover_letter_filename()))
             if p is not None]
    if sys.platform != "darwin":
        return {"id": job_id, "folder": str(app_dir), "revealed": [], "opened": False,
                "note": "Finder is macOS only; the folder is above."}
    if files:
        items = ", ".join(f'POSIX file "{p}"' for p in files)
        script = f'tell application "Finder" to reveal {{{items}}}\ntell application "Finder" to activate'
        done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if done.returncode == 0:
            return {"id": job_id, "folder": str(app_dir), "opened": True,
                    "revealed": [p.name for p in files]}
    # No named copies yet, or Finder refused: the folder itself will do.
    subprocess.run(["open", str(app_dir)], capture_output=True, text=True)
    return {"id": job_id, "folder": str(app_dir), "opened": True,
            "revealed": [p.name for p in files]}


def _fill_state(job_id: str) -> Optional[dict]:
    """The live fill for this job, so the page can show that the agent is
    working and gate a restart behind a confirmation."""
    pid = runner.fill_pid(job_id)
    if pid is None:
        return None
    started = runner.fill_started_at(job_id)
    return {
        "pid": pid,
        "started_at": started,
        "age": round(time.time() - started) if started else None,
    }


def _screen(app_dir: Path) -> Optional[dict]:
    path = app_dir / "screen.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def latest_section(text: str) -> str:
    """The last `---`-separated block of an appended-to artifact."""
    return text.strip().split("\n---\n")[-1].strip() if text.strip() else ""


def _log_path(job_id: str) -> Optional[str]:
    """The apply.py log for this job, if a fill has been attempted."""
    candidate = DATA_DIR / f"apply_{job_id}.log"
    return str(candidate) if candidate.exists() else None


@router.get("/{job_id}/log")
def log(job_id: str, lines: int = 120):
    """The tail of the fill log, so a failure can be read from the page."""
    path = _log_path(job_id)
    if path is None:
        raise HTTPException(404, "no fill has been attempted for this job")
    text = Path(path).read_text(errors="replace").splitlines()
    return PlainTextResponse("\n".join(text[-lines:]))


def _history(app_dir: Path) -> list[dict]:
    import json

    path = app_dir / "status.json"
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("history", [])


@router.get("/{job_id}/file/{name}")
def artifact(job_id: str, name: str):
    """Serve one artifact. Only names in ARTIFACTS are reachable."""
    if name not in ARTIFACTS:
        raise HTTPException(404, "unknown artifact")
    _, app_dir = _job_and_dir(job_id)
    path = app_dir / name
    if not path.exists():
        raise HTTPException(404, f"{name} not in this application")
    # A re-tailor writes a new folder under the same URL, so nothing here
    # may be cached: Chrome's PDF viewer will otherwise keep showing the
    # resume it already rendered.
    headers = {"Cache-Control": "no-store"}
    if ARTIFACTS[name].startswith("text/"):
        return PlainTextResponse(path.read_text(errors="replace"), headers=headers)
    return FileResponse(path, media_type=ARTIFACTS[name], headers=headers)


# What the tailor model is told when the reviewer approves a job it called a
# poor fit. The verdict is a recommendation; approval is the answer to it.
OVERRULE = (
    "You judged this posting a poor fit and tailored nothing. The reviewer has "
    "read that and is applying anyway, so the fit question is settled and is "
    "not yours to reopen: tailor the resume for this posting under every rule "
    "above, and do not reply with MISMATCH."
)


def _freeze(job_id: str, app_dir: Path) -> None:
    """The fit, the resume and the posting as they were at submission
    (career/snapshot.py), once. A failure here never undoes a submission."""
    try:
        from career import snapshot
        snapshot.take(queue.get(job_id), app_dir)
    except Exception:  # noqa: BLE001
        pass


def _bullet_review(app_dir: Path):
    from . import bullets
    try:
        return bullets.detail(app_dir)
    except Exception:  # noqa: BLE001 - an unreadable review never breaks the page
        return None


def _tailored(app_dir: Path) -> bool:
    """Does this folder hold documents to upload? A poor-fit run writes
    `mismatch.md` and nothing else, and a fill there has nothing to attach."""
    return (app_dir / "resume.pdf").exists()


def _overrule_mismatch(job: Job, job_id: str) -> dict:
    """Tailor an untailored job, then fill it, off the request thread.

    A poor-fit verdict stops at checkpoint 1 with no documents. Approving or
    filling it anyway used to launch `apply.py` against an empty folder, which
    skipped with "resume.pdf is missing" and left the job sitting at
    `approved` with nothing to show. The documents are written first, in the
    job's own thread so the page can watch it, and the fill follows them.
    """
    from pipeline import retailor  # lazy: pipeline imports the server package

    def work() -> str:
        app_dir = retailor(job, OVERRULE, keep_status=True)
        pid = runner.start_fill(job_id)
        if pid:
            store.set_status(app_dir, Status.APPROVED, f"filling started (pid {pid})")
        return app_dir.name

    try:
        job_thread.start_change(job_id, OVERRULE, work)
    except job_thread.ThreadError as exc:
        raise HTTPException(409, str(exc))
    return {"id": job_id, "status": Status.APPROVED.value, "filling": False,
            "pid": None, "tailoring": True}


@router.post("/{job_id}/approve")
def approve(job_id: str, decision: Decision) -> dict:
    """Checkpoint 1 passed, and the fill starts straight away.

    Approval is the consent, so making the human then run a command adds a
    step without adding a decision. The fill runs detached and still halts at
    checkpoint 2; nothing about the never-submit guarantee changes.
    """
    job, app_dir = _job_and_dir(job_id)
    if job.status != Status.AWAITING_REVIEW:
        raise HTTPException(409, f"job is {job.status.value}, not awaiting review")
    store.set_status(app_dir, Status.APPROVED, decision.note)
    queue.update(job_id, status=Status.APPROVED)

    if not _tailored(app_dir):
        return _overrule_mismatch(job, job_id)

    pid = runner.start_fill(job_id)
    if pid:
        store.set_status(app_dir, Status.APPROVED, f"filling started (pid {pid})")
    return {"id": job_id, "status": Status.APPROVED.value, "filling": bool(pid),
            "pid": pid}


# Every state a fill may be started from. FILLING is included deliberately: a
# run whose browser died leaves the job sitting there, and the reviewer needs
# to be able to start another without first repairing the state by hand.
REFILLABLE = (Status.APPROVED, Status.FILLING, Status.FILLED, Status.FAILED)
FILL_MODES = ("autopilot", "jobright_check")


@router.post("/{job_id}/fill")
def fill_now(job_id: str, request: FillRequest) -> dict:
    """Start or restart the fill. Repeatable, but never concurrent.

    One fill per job at a time: a second apply.py on the same Chrome tab
    fights the first. If one is running, the request is refused with the pid
    unless `force` is set, in which case the running fill is killed first.
    The page only sends `force` after the human has confirmed.
    """
    job, app_dir = _job_and_dir(job_id)
    if request.mode not in FILL_MODES:
        raise HTTPException(422, f"fill mode must be one of: {', '.join(FILL_MODES)}")
    if job.status not in REFILLABLE:
        raise HTTPException(
            409, f"job is {job.status.value}; approve it before filling"
        )
    if not _tailored(app_dir):
        # Nothing was tailored (a poor-fit verdict). Write the documents
        # first; the fill starts when they land.
        return _overrule_mismatch(job, job_id)
    running = runner.fill_pid(job_id)
    if running and not request.force:
        raise HTTPException(
            409, {"error": "fill_running", "pid": running,
                  "message": f"a fill is already running for this job (pid {running})"},
        )
    if running:
        runner.stop_fill(job_id)
        store.set_status(app_dir, Status.APPROVED, f"fill {running} killed for a restart")
    pid = runner.launch(
        "apply.py", job_id, env={"AUTOPILOT_FILL_MODE": request.mode},
    )
    if not pid:
        raise HTTPException(500, "could not start apply.py")
    # Back to APPROVED so apply.py, which only touches approved jobs, will
    # pick it up, and so a stuck FILLING cannot wedge the job permanently.
    store.set_status(
        app_dir, Status.APPROVED,
        f"{request.mode.replace('_', ' ')} fill started (pid {pid})",
    )
    queue.update(job_id, status=Status.APPROVED)
    return {"id": job_id, "filling": True, "pid": pid}


@router.post("/{job_id}/reject")
def reject(job_id: str, rejection: Rejection) -> dict:
    """Drop the application, recording why.

    A reason is required: a rejected job stays visible in the list, and a
    rejection with no reason tells you nothing three weeks later. The folder
    is kept intact as the record of what was tried.
    """
    job, app_dir = _job_and_dir(job_id)
    label = REJECT_LABELS[rejection.reason]
    detail = f"{label}: {rejection.note}" if rejection.note else label
    stopped = runner.stop_fill(job_id) if job.status == Status.FILLING else None

    store.set_status(app_dir, Status.SKIPPED, detail)
    store.write_or_append(app_dir, "rejection.md", f"# Rejected\n\n**{label}**\n\n"
                          f"{rejection.note or '_no further detail_'}\n")
    queue.update(
        job_id,
        status=Status.SKIPPED,
        reject_reason=rejection.reason,
        reject_note=rejection.note,
    )
    return {
        "id": job_id,
        "status": Status.SKIPPED.value,
        "reason": label,
        "stopped_fill": stopped,
    }


@router.delete("/{job_id}")
def delete_job(job_id: str) -> dict:
    """Permanently remove a job, every archive version, and its thread."""
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    if job.status == Status.TAILORING:
        raise HTTPException(409, "this job is still tailoring; wait for it to finish before deleting")
    if job_thread.running(job_id):
        raise HTTPException(409, "document regeneration is still running; wait for it to finish before deleting")

    stopped = runner.stop_fill(job_id)
    folders = store.delete_job(job_id)
    job_thread.delete(job_id)
    queue.remove(job_id)
    return {
        "id": job_id,
        "deleted": True,
        "folders": folders,
        "stopped_fill": stopped,
    }


# Everything a retry may be started from: a run that fell over, a job the
# person rejected and has changed their mind about, and the live states a run
# can die in the middle of. SUBMITTED is the one that is not here - the
# application has gone, the row is the record of it, and the way to work on
# it again is the thread's re-tailor.
RETRYABLE = (Status.QUEUED, Status.TAILORING, Status.AWAITING_REVIEW,
             Status.APPROVED, Status.FILLING, Status.FILLED, Status.FAILED,
             Status.SKIPPED)


@router.post("/{job_id}/retry")
def retry(job_id: str) -> dict:
    """Start this job again from wherever it has nothing.

    A job can stop for a reason that has nothing to do with the job: the key
    ran out of credit (`OpenRouter returned 402: Insufficient credits`, which
    is what prompted this), a fetch timed out, the socket dropped. The row
    went to `failed`, which lives on a closed shelf with no buttons on it, so
    a perfectly good posting was unreachable - there was nothing to press
    (2026-10-09, asked for).

    From the top means from the first step that has nothing to show:

    - no `resume.pdf` in the folder, so there is nothing to attach: the
      pipeline runs again, which screens, fetches, tailors and compiles, and
      the job comes back to checkpoint 1 for the person as usual.
    - documents already there: the fill runs again instead, because
      re-tailoring a resume that compiled is spending a model call to get the
      same thing twice.

    A retry is a restart, so whatever is still running for this job is
    stopped first. Nothing here submits, nothing is approved on the person's
    behalf past checkpoint 1 that was not approved before, and the fill goes
    through `runner.start_fill`, which runs one fill per job.
    """
    job, app_dir = _job_and_dir(job_id)
    if job.status == Status.SUBMITTED:
        raise HTTPException(409, "this application has been submitted; "
                                 "ask for a change in the thread instead")
    if job.status not in RETRYABLE:
        raise HTTPException(409, f"job is {job.status.value} and cannot be retried")

    was = job.status.value
    note = f"retried from {was}"
    killed = runner.stop_fill(job_id)
    if killed:
        note += f" (fill {killed} stopped)"
    tailoring = runner.stop_pipeline(job_id)
    if tailoring:
        note += f" (tailoring {tailoring} stopped)"
    # The rejection is lifted, because the job is being tried again: a row
    # still carrying "poor fit" while it tailors says two things at once.
    if job.reject_reason or job.reject_note:
        note += " (rejection lifted)"

    if _tailored(app_dir):
        # `apply.py` only touches an approved job, and the error has to go
        # with it: a rerun that works still shows the old failure otherwise.
        store.set_status(app_dir, Status.APPROVED, note)
        queue.update(job_id, status=Status.APPROVED, error=None,
                     reject_reason=None, reject_note=None)
        pid = runner.start_fill(job_id)
        if pid:
            store.set_status(app_dir, Status.APPROVED, f"filling started (pid {pid})")
        return {"id": job_id, "was": was, "step": "fill", "status": Status.APPROVED.value,
                "filling": bool(pid), "pid": pid, "killed": killed}

    store.set_status(app_dir, Status.QUEUED, f"{note}; tailoring again")
    queue.update(job_id, status=Status.QUEUED, error=None,
                 reject_reason=None, reject_note=None)
    pid = runner.start_pipeline(job_id)
    if not pid:
        raise HTTPException(500, "could not start pipeline.py")
    return {"id": job_id, "was": was, "step": "tailor", "status": Status.QUEUED.value,
            "filling": False, "pid": pid, "killed": killed}


@router.get("/meta/reject-reasons")
def reject_reasons() -> list[dict]:
    """The reason list the review page offers."""
    return [{"value": reason.value, "label": label} for reason, label in REJECT_LABELS.items()]


@router.post("/{job_id}/submitted")
def mark_submitted(job_id: str, decision: Decision) -> dict:
    """Checkpoint 2. Only a human reaches this, after submitting by hand.

    The agent has no path to SUBMITTED: its terminal state is FILLED, and this
    endpoint is reachable only from the review page.
    """
    job, app_dir = _job_and_dir(job_id)
    # FILLING is accepted too: the human may finish and submit the form while
    # the agent is still poking at it. Their submission wins; the agent stops.
    if job.status not in (Status.FILLED, Status.FILLING):
        raise HTTPException(409, f"job is {job.status.value}, not filled")
    killed = runner.stop_fill(job_id)
    note = decision.note or "submitted by hand"
    if killed:
        note += f" (fill {killed} still running, killed)"
    store.set_status(app_dir, Status.SUBMITTED, note)
    queue.update(job_id, status=Status.SUBMITTED)
    _freeze(job_id, app_dir)

    # One last look at the form before its tab goes, so the changes the
    # human may still pick to remember are the final ones. Never fails
    # the submission; nothing is learned by itself.
    corrections.capture(job.url, app_dir)

    # Only this job's tab goes. The browser is the person's working set:
    # the Jobright list, the next job's form, their logins. Closing the
    # whole thing here read as a crash (2026-09-19).
    tab = "closed" if chrome.close_tab(_form_tab(job, app_dir)) else "not_open"
    return {"id": job_id, "status": Status.SUBMITTED.value, "tab": tab,
            "changes": corrections.changes(app_dir)}


def _form_tab(job: Job, app_dir: Path) -> str:
    """The tab holding this job's form: the id the fill recorded when it is
    still open, else the tab on the same form URL, else ""."""
    from browser import forms

    saved = corrections._read(app_dir / corrections.FILL_REPORT)
    return forms.find_target(corrections.cdp_url(), job.url, str(saved.get("target_id") or ""))


@router.post("/{job_id}/files")
def files_for_the_page(job_id: str) -> dict:
    """The tailored PDFs, base64, under their upload names.

    The capture script on the employer's page asks for these and shows them
    as draggable chips, so the person can drop a file on the form's own
    slot when the agent is slow or when they would rather do it by hand.
    POST because the page's bridge only posts; the body is unused. Base64
    because the bridge carries json, not bytes. Two PDFs is a few hundred
    KB, once per page.
    """
    import base64

    import apply as apply_script  # lazy: apply.py pulls in the browser stack

    _, app_dir = _job_and_dir(job_id)
    out = {}
    for key, source, stem in (("resume", "resume.pdf", apply_script.resume_filename()),
                              ("cover_letter", "cover_letter.pdf", apply_script.cover_letter_filename())):
        path = app_dir / source
        if path.exists():
            out[key] = {"name": f"{stem}.pdf", "type": "application/pdf",
                        "b64": base64.b64encode(path.read_bytes()).decode()}
    return out


class Confirmation(BaseModel):
    url: str = ""
    quote: str = ""


@router.post("/{job_id}/submitted-seen")
def confirmation_seen(job_id: str, seen: Confirmation) -> dict:
    """The page in the browser showed a submission confirmation.

    Reported by the capture script watching the filled form's tab after the
    human pressed submit, and by `server/watch.py`, which reads the tab
    itself; the agent has no path here either, it never reaches the page
    after its own last action. Marks the job like the button does, but
    never closes the browser: the person is looking at it. A job that is
    not filled or filling is left alone, so a stray "thank you" on an
    unrelated page changes nothing.
    """
    job, app_dir = _job_and_dir(job_id)
    return mark_seen(job, app_dir, seen.url, seen.quote, "on the page")


def mark_seen(job: Job, app_dir: Path, url: str = "", quote: str = "", by: str = "") -> dict:
    """Submitted, because a confirmation was seen in the browser. Only a
    filled or filling job; the fill, if still running, stops; the last
    look at the form is what gets learned. Never closes anything."""
    if job.status not in (Status.FILLED, Status.FILLING):
        return {"id": job.id, "status": job.status.value, "marked": False}
    killed = runner.stop_fill(job.id)
    note = f"confirmation seen {by}".strip()
    if quote:
        note += f": “{quote[:120]}”"
    if url:
        note += f" at {url}"
    if killed:
        note += f" (fill {killed} still running, killed)"
    store.set_status(app_dir, Status.SUBMITTED, note)
    queue.update(job.id, status=Status.SUBMITTED)
    _freeze(job.id, app_dir)
    # The page has moved on to the confirmation; the last state the tab
    # reported before that is what the human can still pick from.
    return {"id": job.id, "status": Status.SUBMITTED.value, "marked": True,
            "changes": corrections.changes(app_dir)}


@router.post("/{job_id}/form-state")
def form_state(job_id: str) -> dict:
    """The tab says the form may have changed: look at it now. Sent by the
    capture script while the human works on a filled form and on its way
    out. Only a filled or filling job has a form worth looking at."""
    job, app_dir = _job_and_dir(job_id)
    if job.status not in (Status.FILLED, Status.FILLING):
        return {"id": job_id, "captured": False, "reason": job.status.value,
                "changes": corrections.changes(app_dir)}
    return {"id": job_id, **corrections.capture(job.url, app_dir), "changes": corrections.changes(app_dir)}


class Remember(BaseModel):
    labels: list[str]


@router.get("/{job_id}/changes")
def form_changes(job_id: str) -> dict:
    """What the human changed on this form since the fill: for the
    banner's and the page's pick list. Read, never decided."""
    _, app_dir = _job_and_dir(job_id)
    return {"id": job_id, "changes": corrections.changes(app_dir)}


@router.post("/{job_id}/remember")
def remember_changes(job_id: str, body: Remember) -> dict:
    """The human's pick of which changes to keep for every next form.
    The only way a correction gets on file."""
    _, app_dir = _job_and_dir(job_id)
    result = corrections.remember(app_dir, body.labels)
    return {"id": job_id, **result, "changes": corrections.changes(app_dir)}


@router.post("/{job_id}/revise")
def revise(job_id: str, revision: Revision) -> dict:
    """Re-tailor with an extra instruction, into a new application folder.

    Imported here rather than at module scope: pipeline imports the server
    package, so importing it at the top would be circular.
    """
    from pipeline import retailor

    job, _ = _job_and_dir(job_id)
    if not revision.instruction.strip():
        raise HTTPException(400, "instruction is empty")
    app_dir = retailor(job, revision.instruction)
    return {
        "id": job_id,
        "folder": app_dir.name,
        "status": (queue.get(job_id) or job).status.value,
    }
