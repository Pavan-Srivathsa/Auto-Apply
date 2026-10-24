"""The Prompts tab, the Workshop, and first-run setup.

- `/prompts`: every system prompt, the person's version of it, its stock
  text, its history, and a pending update conflict when there is one.
- `/workshop`: requests in words, turned into prompt edits by
  `tailor/workshop.py`; applied only on the person's click.
- `/setup`: the first-run onboarding. What is still missing before the
  first job (the OpenRouter key, the Jobright extension, the master
  resume, Chrome), the key itself, and the Jobright pages opened in the
  Autopilot Chrome. The key is typed into the page, checked against
  OpenRouter, and written to the data directory's `.env`; it is never sent
  anywhere else, and never through Claude Code's transcript. Whether the
  person is signed in to Jobright is not read (that would mean reading
  their cookies); they say so with a tick.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import paths
from browser import chrome
from tailor import prompts, resume_bases, tailor, workshop
from tex import compile as texc

from . import queue, settings

router = APIRouter()

KEY_CHECK_URL = "https://openrouter.ai/api/v1/key"
KEY_SHAPE = re.compile(r"^sk-or-[A-Za-z0-9_-]{20,}$")

# Jobright's Chrome extension: what autofills the forms and puts the Apply
# tab in front of the banner.
JOBRIGHT_EXTENSION = "odcnpipkhjegpefkfplmedhmkmmhmoko"
# The only pages onboarding opens; `/setup/open` takes a name, never a URL.
PAGES = {
    "jobright_extension": f"https://chromewebstore.google.com/detail/{JOBRIGHT_EXTENSION}",
    "jobright": "https://jobright.ai/",
    "openrouter": "https://openrouter.ai/settings/keys",
}


class PromptText(BaseModel):
    text: str


class Version(BaseModel):
    version: str


class WorkshopRequest(BaseModel):
    request: str


class KeyRequest(BaseModel):
    key: str


class OpenRequest(BaseModel):
    page: str


class ResumeUpload(BaseModel):
    name: str
    text: str
    base_id: Optional[str] = None
    label: str = ""
    description: str = ""


class ResumeActive(BaseModel):
    active: bool


# A resume is a few kilobytes of LaTeX; anything near this is not one.
RESUME_MAX = 200_000
TEMPLATE = paths.ROOT / "base" / "resume.template.tex"


def _prompt_or_404(fn, *args):
    try:
        return fn(*args)
    except prompts.PromptError as exc:
        raise HTTPException(404, str(exc)) from None


# ------------------------------------------------------------ prompts --


@router.get("/prompts")
def list_prompts() -> dict:
    return {"prompts": prompts.listing()}


@router.get("/prompts/{name}")
def get_prompt(name: str) -> dict:
    return _prompt_or_404(prompts.detail, name)


@router.post("/prompts/{name}")
def save_prompt(name: str, body: PromptText) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "a prompt cannot be empty; Reset brings the stock text back")
    return _prompt_or_404(prompts.save, name, body.text)


@router.post("/prompts/{name}/reset")
def reset_prompt(name: str) -> dict:
    return _prompt_or_404(prompts.reset, name)


@router.post("/prompts/{name}/restore")
def restore_prompt(name: str, body: Version) -> dict:
    return _prompt_or_404(prompts.restore, name, body.version)


# ----------------------------------------------------------- workshop --


def _workshop(fn, *args):
    try:
        return fn(*args)
    except workshop.WorkshopError as exc:
        raise HTTPException(400, str(exc)) from None
    except workshop.llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/workshop")
def get_workshop() -> dict:
    return {"proposals": workshop.proposals()[-50:],
            "unseen": len(workshop.unseen()),
            "learn_every": workshop.LEARN_EVERY}


@router.post("/workshop")
def ask_workshop(body: WorkshopRequest) -> dict:
    return _workshop(workshop.propose, body.request)


@router.post("/workshop/learn")
def learn_now() -> dict:
    proposal = _workshop(workshop.learn)
    return {"proposal": proposal}


@router.post("/workshop/{pid}/apply")
def apply_proposal(pid: str) -> dict:
    return _workshop(workshop.apply, pid)


@router.post("/workshop/{pid}/dismiss")
def dismiss_proposal(pid: str) -> dict:
    return _workshop(workshop.dismiss, pid)


# -------------------------------------------------------------- setup --


def key_is_set() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def jobright_installed() -> bool:
    """The extension is in the Autopilot Chrome's profile, the one the
    forms are filled in; the person's everyday Chrome does not count."""
    from browser.fill import profile_dir
    return (profile_dir() / "Default" / "Extensions" / JOBRIGHT_EXTENSION).is_dir()


def onboarded() -> bool:
    """Past the first-run screens: said so, or already has jobs (a copy
    that was in use before onboarding existed never sees it)."""
    return bool(settings.load().get("onboarded")) or bool(queue.all_jobs())


@router.get("/setup")
def setup_status() -> dict:
    """What onboarding and the setup bar list. Each item is done or not."""
    bases = resume_bases.all_bases()
    rows = []
    all_problems = []
    for base in bases:
        problems = tailor.master_problems(
            base.tex_path.read_text(errors="replace")
        ) if base.tex_path.exists() else ["resume.tex is missing"]
        all_problems.extend(f"{base.label}: {problem}" for problem in problems)
        rows.append(base.public() | {
            "primary": bool(bases) and base.id == resume_bases.primary_id(),
            "problems": problems,
            "ready": base.active and base.pdf_path.exists() and not problems,
        })
    ready = [row for row in rows if row["ready"]]
    return {
        "home": str(paths.HOME),
        "onboarded": onboarded(),
        "key": key_is_set(),
        "jobright": jobright_installed(),
        "resume": bool(ready),
        "resume_problems": all_problems,
        "resume_path": str(paths.BASE / "resume.tex"),
        "resumes": rows,
        "primary_resume": resume_bases.primary_id() if bases else None,
        "chrome": chrome.find_browser() is not None,
    }


def write_env(path, name: str, value: str) -> None:
    """Set one variable in a .env file, keeping every other line."""
    lines = path.read_text().splitlines() if path.exists() else []
    line = f"{name}={value}"
    for i, existing in enumerate(lines):
        if re.match(rf"^\s*#?\s*{re.escape(name)}=", existing):
            lines[i] = line
            break
    else:
        lines.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def check_key(key: str) -> None:
    """Raises HTTPException unless OpenRouter accepts the key."""
    try:
        response = httpx.get(KEY_CHECK_URL, headers={"Authorization": f"Bearer {key}"},
                             timeout=15)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach OpenRouter: {exc}") from None
    if response.status_code in (401, 403):
        raise HTTPException(400, "OpenRouter refused this key. Copy it again from "
                                 "openrouter.ai/settings/keys.")
    if response.status_code >= 400:
        raise HTTPException(502, f"OpenRouter answered {response.status_code}")
    # A management (provisioning) key passes this read and fails every model
    # call with "User not found".
    try:
        provisioning = (response.json().get("data") or {}).get("is_provisioning_key")
    except ValueError:
        provisioning = False
    if provisioning:
        raise HTTPException(400, "This is a management key; models refuse it. Create a regular "
                                 "API key at openrouter.ai/settings/keys.")


@router.post("/setup/open")
def open_page(body: OpenRequest) -> dict:
    """Open a Jobright or OpenRouter page as a tab in the Autopilot Chrome,
    where the extension has to live, whichever browser shows this page."""
    url = PAGES.get(body.page)
    if url is None:
        raise HTTPException(404, f"no page called {body.page!r}")
    request = urllib.request.Request(
        f"{chrome.cdp_url(chrome.port())}/json/new?{url}", method="PUT")
    try:
        urllib.request.urlopen(request, timeout=5).read()
    except OSError:
        return {"opened": False, "url": url}
    return {"opened": True, "url": url}


@router.get("/setup/template")
def resume_template() -> FileResponse:
    """The layout the tailor reads, to download and fill in."""
    return FileResponse(TEMPLATE, media_type="application/x-tex",
                        filename="autopilot-resume-template.tex")


@router.post("/setup/resume")
def upload_resume(body: ResumeUpload) -> dict:
    """Add or replace one master resume, in the person's own LaTeX design.

    LaTeX only, on purpose: the tailor edits the source and every word of
    it is the person's. It must compile and expose a readable experience
    section before it enters the active resume registry.
    """
    text = body.text
    if not body.name.lower().endswith(".tex"):
        raise HTTPException(400, "The master resume is the LaTeX source: a .tex file, not a PDF "
                                 "or a Word document.")
    if len(text) > RESUME_MAX or "\\documentclass" not in text or "\\begin{document}" not in text:
        raise HTTPException(400, "That is not a whole LaTeX document (no \\documentclass or "
                                 "\\begin{document}).")
    problems = tailor.master_problems(text)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    bases = resume_bases.all_bases()
    base_id = body.base_id
    if base_id is None and len(bases) == 1 and not body.label.strip():
        base_id = bases[0].id
    existing = None
    if base_id:
        try:
            existing = resume_bases.get(base_id)
        except KeyError:
            raise HTTPException(404, f"no resume base called {base_id!r}") from None
    label = body.label.strip() or (existing.label if existing else Path(body.name).stem)
    description = body.description.strip() or (existing.description if existing else "")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work / "resume.tex"
            source.write_text(text)
            assets = existing.directory if existing else paths.BASE
            if assets.exists():
                for sibling in assets.iterdir():
                    if sibling.is_file() and sibling.suffix in (".cls", ".sty"):
                        shutil.copy2(sibling, work / sibling.name)
            pdf = texc.compile_pdf(source, work / "resume.pdf")
            row = resume_bases.save_base(base_id, label, description, text, pdf)
            if assets.exists() and assets != row.directory:
                for sibling in assets.iterdir():
                    if sibling.is_file() and sibling.suffix in (".cls", ".sty"):
                        shutil.copy2(sibling, row.directory / sibling.name)
    except texc.CompileError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a timeout or a missing engine, said on the page
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from None
    return setup_status() | {"compiled": True, "resume_base": row.public()}


@router.get("/setup/resume/{base_id}/pdf")
def resume_base_pdf(base_id: str) -> FileResponse:
    try:
        base = resume_bases.get(base_id)
    except KeyError:
        raise HTTPException(404, f"no resume base called {base_id!r}") from None
    if not base.pdf_path.exists():
        raise HTTPException(404, "this resume has no compiled PDF")
    return FileResponse(base.pdf_path, media_type="application/pdf",
                        filename=f"{base.id}.pdf", headers={"Cache-Control": "no-store"})


@router.post("/setup/resume/{base_id}/primary")
def primary_resume(base_id: str) -> dict:
    try:
        resume_bases.set_primary(base_id)
    except KeyError:
        raise HTTPException(404, f"no active resume called {base_id!r}") from None
    return setup_status()


@router.post("/setup/resume/{base_id}/active")
def active_resume(base_id: str, body: ResumeActive) -> dict:
    try:
        resume_bases.set_active(base_id, body.active)
    except KeyError:
        raise HTTPException(404, f"no resume called {base_id!r}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return setup_status()


@router.post("/setup/resume/{base_id}/delete")
def delete_resume(base_id: str) -> dict:
    try:
        base = resume_bases.get(base_id)
    except KeyError:
        raise HTTPException(404, f"no resume called {base_id!r}") from None
    if base.active:
        raise HTTPException(409, "archive this resume before deleting it")
    if any(job.resume_base == base.id for job in queue.all_jobs()):
        raise HTTPException(
            409, "this resume is still selected by an existing job; switch that job first"
        )
    try:
        resume_bases.delete_base(base.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return setup_status()


@router.post("/setup/done")
def setup_done() -> dict:
    settings.save(onboarded=True)
    return setup_status()


@router.post("/setup/key")
def set_key(body: KeyRequest) -> dict:
    key = body.key.strip()
    if not KEY_SHAPE.match(key):
        raise HTTPException(400, "That does not look like an OpenRouter key; they start "
                                 "with sk-or-.")
    check_key(key)
    write_env(paths.ENV_FILE, "OPENROUTER_API_KEY", key)
    # The server and every script it starts from now on see it at once.
    os.environ["OPENROUTER_API_KEY"] = key
    return setup_status()
