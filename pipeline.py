"""Phase 2 pipeline: queued job -> tailored, compiled, archived, awaiting review.

This is the half that runs before checkpoint 1. It deliberately stops at
AWAITING_REVIEW; the browser fill loop is a separate phase and only ever runs
against a job a human has approved.

    python pipeline.py            # process every queued job
    python pipeline.py <job_id>   # process one
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import traceback
import urllib.parse
from pathlib import Path
from typing import Optional

import paths
from archive import store
from browser import autofill, chrome
from server import postings, queue, runner, seen, settings
from server import screen as screen_server
from server.models import Job, Status
from tailor import bank
from tailor import brief as briefs
from tailor import cover, critic, fetch, layout, profile, quality, qualify, resume_bases, reviewer, tailor
from tex import compile as texc
from tex import mark

ROOT = Path(__file__).resolve().parent


def base_page_count(base_tex: Optional[Path] = None,
                    base_pdf: Optional[Path] = None) -> int:
    """Page count of the master resume, which the tailored one must match.

    Falls back to 1, since a one-page resume is the assumption this project is
    built around.
    """
    base_tex = base_tex or paths.BASE / "resume.tex"
    base_pdf = base_pdf or base_tex.with_suffix(".pdf")
    if base_pdf.exists():
        return texc.page_count(base_pdf) or 1
    if not base_tex.exists():
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        pdf = texc.compile_pdf(base_tex, Path(tmp) / "base.pdf")
        return texc.page_count(pdf) or 1


def make_page_check(base_tex: Path):
    """Compile a candidate resume and report its page count.

    Sibling files of the master resume travel with it, so a template split
    across a .cls or .sty still compiles. A candidate that fails to compile
    returns None, which the tailor treats as "unknown" and accepts; the real
    compile later in the pipeline will surface the error properly.
    """
    def check(candidate_tex: str):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            src = work / "candidate.tex"
            src.write_text(candidate_tex)
            for sibling in base_tex.parent.iterdir():
                if sibling.is_file() and sibling.suffix in (".cls", ".sty"):
                    (work / sibling.name).write_bytes(sibling.read_bytes())
            try:
                pdf = texc.compile_pdf(src, work / "candidate.pdf")
            except texc.CompileError:
                return None
            return texc.page_count(pdf)

    return check


def retailor(job: Job, instruction: str, keep_status: bool = False) -> Path:
    """Run the job again with a reviewer instruction added to the prompt.

    Produces a new application folder rather than editing the existing one,
    so the rejected version stays on disk next to the reason it was rejected.

    `keep_status` is the re-tailor of a job that is already filled or
    submitted: the documents are written and the row points at them, but
    the job does not walk back to checkpoint 1 and no fill is started.
    The new resume is there to be dragged onto a form by hand.
    """
    return process(job, extra_instruction=instruction, keep_status=keep_status)


def fetch_posting(job: Job) -> fetch.Posting:
    """The posting text: the best of what the server fetched, what the
    browser saw on the employer's page, and Jobright's copy.

    An Apply button can land on a bare application form, and an ATS page can
    render client-side, so the fetch may come back with a page that clears
    the length gate on cookie notices and field labels alone. Each copy is
    scored on what a description is made of (`tailor.quality`, no model);
    the fetched page wins ties, being the posting itself, and loses when
    it has clearly less than the best copy. Which one won is written into
    the posting's header.
    """
    candidates: list[tuple[str, fetch.Posting]] = []
    fetched: fetch.Posting | None = None
    error: Exception | None = None
    try:
        fetched = fetch.fetch(job.url)
        candidates.append(("employer page", fetched))
    except Exception as exc:  # noqa: BLE001 - the fallbacks decide
        error = exc
    for saved in postings.fallbacks(job.id, job.url):
        name = "Jobright's copy" if seen.is_source_page(saved.url) else "the page the browser saw"
        candidates.append((name, fetch.Posting(url=job.url, text=saved.text, title=saved.title,
                                               company=saved.company)))
    if not candidates:
        raise error or RuntimeError(f"no posting text for {job.url}")

    # The board's own API answered: the employer's copy of the posting, with
    # nothing to score it against. A browser's copy of a Greenhouse form is
    # longer than the description inside it, and would have won (upstream e2c600b).
    if fetched is not None and fetched.authoritative:
        fetched.text_source = "the job board"
        if not fetched.company:
            fetched.company = next((p.company for _, p in candidates if p.company), "")
        return fetched
    # A page that is the application form, not the description, is never the posting.
    scores = [(0 if quality.looks_like_form(p.text) else quality.score(p.text), i)
              for i, (_, p) in enumerate(candidates)]
    top_score, top = max(scores, key=lambda item: (item[0], -item[1]))
    if top_score == 0 and fetched is None:
        raise error or RuntimeError(f"no posting text for {job.url}")
    if fetched is not None and (top_score == 0 or scores[0][0] >= top_score * quality.GOOD_ENOUGH):
        top = 0
    name, posting = candidates[top]
    if top != 0 or error is not None:
        why = f"fetch failed ({error})" if error else f"fetched page scored {scores[0][0]} against {top_score}"
        print(f"{why}; using {name} ({candidates[top][1].url})")
    posting.text_source = name
    # The fetched page may have no company; Jobright's copy usually does.
    if not posting.company:
        posting.company = next((p.company for _, p in candidates if p.company), "")
    if not posting.title:
        posting.title = next((p.title for _, p in candidates if p.title), "")
    return posting


# The employer's name, read off the page title or the ATS URL when no
# metadata carries it. Greenhouse's embed exposes none, so a folder was named
# `unknown-company_job-application-for-software-eng_6561` while the row's own
# title said "... at SeatGeek" and the URL said `for=seatgeek`.
TITLE_COMPANY = (
    re.compile(r"\bat\s+([A-Z][^|@·]{1,40}?)\s*(?:[|·]|\Z)"),   # "... at SeatGeek"
    re.compile(r"@\s*([^|·]{1,40}?)\s*(?:[|·]|\Z)"),             # "Role @ Sim"
    re.compile(r"\|\s*([^|]{1,40}?)\s*\|"),                     # "...-New Grad |Helios|"
)
# The slug an ATS puts in its own URL. Last resort: it is lower case, so
# "seatgeek" comes back as "Seatgeek" where the title would have said SeatGeek.
URL_COMPANY = (
    re.compile(r"[?&]for=([A-Za-z0-9._-]+)"),
    re.compile(r"jobs\.lever\.co/([A-Za-z0-9._-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)"),
    re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9._-]+)"),
    re.compile(r"([A-Za-z0-9-]+)\.wd\d+\.myworkdayjobs\.com"),
    re.compile(r"([A-Za-z0-9-]+)\.bamboohr\.com"),
    re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9._-]+)"),
    re.compile(r"([A-Za-z0-9-]+)\.avature\.net"),
)
# Words a title puts after "at" that are not an employer.
NOT_A_COMPANY = re.compile(r"^(?:a|an|the|our|least|this|scale up|home)\b", re.I)
# A title says "at Billtrust US Careers"; the employer is Billtrust.
TITLE_TAIL = re.compile(r"\s+(?:US\s+)?(?:careers?|jobs?|job board|hiring|greenhouse)\s*$", re.I)


def company_from(title: str, url: str) -> str:
    """The employer named in a page title, else the ATS slug in the URL.

    Only a fallback: Jobright's copy and the posting itself are asked first,
    and either beats a slug. Returns "" when neither says anything.
    """
    for pattern in TITLE_COMPANY:
        match = pattern.search(title or "")
        if not match:
            continue
        name = TITLE_TAIL.sub("", " ".join(match.group(1).split())).strip(" -–—,.")
        if name and not NOT_A_COMPANY.match(name):
            return name
    for pattern in URL_COMPANY:
        match = pattern.search(url or "")
        if not match:
            continue
        slug = urllib.parse.unquote(match.group(1)).replace("_", " ").replace(".", " ")
        words = [word for word in re.split(r"[-\s]+", slug) if word]
        if words and words[0].lower() not in ("external", "careers", "jobs", "embed"):
            return " ".join(word[:1].upper() + word[1:] for word in words)
    return ""


def allocate(job: Job) -> Path:
    """A fresh application folder, recorded on the queue row."""
    app_dir = store.create(job)
    store.set_status(app_dir, Status.TAILORING)
    queue.update(job.id, app_dir=str(app_dir))
    return app_dir


def write_resume(app_dir: Path, posting: fetch.Posting, stories: list[str],
                 extra_instruction: str = "", model: Optional[str] = None,
                 base: Optional[resume_bases.ResumeBase] = None,
                 choice: Optional[resume_bases.Choice] = None):
    """Tailor and compile only the resume, returning its result and status note."""
    base = base or resume_bases.get()
    base_tex = base.tex_path
    master = base_tex.read_text()
    target = base_page_count(base_tex, base.pdf_path)
    choice = choice or resume_bases.Choice(
        base.id, "sticky", "high", "Kept this job's selected resume.",
    )
    store.write(app_dir, "resume_base.json", json.dumps(choice.to_dict(), indent=2) + "\n")
    store.write(app_dir, "master_resume.tex", master)
    if base.pdf_path.exists():
        store.write(app_dir, "master_resume.pdf", base.pdf_path.read_bytes())
    resume_bases.copy_assets(base, app_dir)
    profile_text = tailor.load_profile(stories=stories)
    brief = write_brief(app_dir, posting, master, profile_text)
    options = ""
    bank_stories: list[str] = []
    if brief:
        write_qualify(app_dir, brief, master, profile_text)
        options, bank_stories = write_bullets(app_dir, brief, master)
        if bank_stories:
            # The banked bullets' facts are in their jobs' stories: the tailoring's
            # invention check has to see those stories too.
            profile_text = tailor.load_profile(stories=list(dict.fromkeys(stories + bank_stories)))
    result = tailor.tailor(
        posting,
        resume_tex=master,
        page_check=make_page_check(base_tex),
        target_pages=target,
        extra_instruction=extra_instruction,
        profile=profile_text,
        model=model,
        layout_pdf=base.pdf_path,
        brief=brief.as_message() if brief else "",
        options=options,
    )
    result = review_resume(app_dir, posting, master, result, brief, profile_text, base, model, options)
    result = verify_bullets(app_dir, posting, master, result, brief, profile_text,
                            stories + (bank_stories if brief else []), base, model, options)
    store.write(app_dir, "resume.tex", result.tex)
    store.write(app_dir, "resume.diff", result.diff or "(no changes)\n")
    store.write(
        app_dir,
        "suggestions.md",
        f"# Tailoring rationale\n\n_Model: {result.model} "
        f"(attempt {result.attempts})_\n\n"
        f"**Resume base:** {base.label} ({choice.source}, {choice.confidence}) — "
        f"{choice.reason}\n\n"
        + (f"**Fit:** {result.verdict}\n\n" if result.verdict else "")
        + (
            "**Length warnings:** " + "; ".join(result.warnings) + "\n\n"
            if result.warnings
            else ""
        )
        + (
            "**Attempts rejected before this one:**\n"
            + "\n".join(f"- {line}" for line in result.rejections) + "\n\n"
            if result.rejections
            else ""
        )
        + f"{result.suggestions}\n",
    )

    pdf = texc.compile_pdf(app_dir / "resume.tex", app_dir / "resume.pdf")
    pages = texc.page_count(pdf)
    write_marked(app_dir, master, result.tex)
    note = f"{pages} page(s)"
    if pages is not None and pages != target:
        note += f" — master is {target}; the page-count gate did not hold"
    return result, note


def process(job: Job, extra_instruction: str = "", keep_status: bool = False) -> Path:
    """Fetch, tailor, compile, archive. Returns the application folder.

    `keep_status` leaves the queue row's status alone (see `retailor`): the
    folder is written and the row points at it, but a filled or submitted
    job stays filled or submitted and nothing is approved or launched.
    """
    # A fresh run clears the last one's error; otherwise a retry that
    # succeeds still shows "Failed" over a perfectly good resume.
    held = (queue.get(job.id) or job).status if keep_status else None
    # A failed run leaves no standing worth keeping. Documents that land now
    # are the job coming back to checkpoint 1, not a replacement resume
    # beside a finished application, and a job left at `failed` sits in a
    # closed shelf where the new resume is never seen. The fill is still not
    # started by this path; the human approves it as usual.
    revive = held == Status.FAILED
    if revive:
        held = None
    if keep_status:
        queue.update(job.id, error=None)
    else:
        queue.update(job.id, status=Status.TAILORING, error=None)

    # Fetch before the folder is allocated: its name carries the company, and
    # the extension often captures none (a Jobright link that lands on Ashby
    # exposes no metadata), so every first run was `unknown-company_...`.
    try:
        posting = fetch_posting(job)
    except Exception:
        # A failed fetch still gets its own folder, so the error lands next
        # to this run's record and not in the previous run's.
        allocate(job)
        raise

    # The posting is authoritative for company and title; the extension only
    # guessed them from whatever metadata the page happened to expose, which
    # on Jobright is "Job Recommendations | Jobright AI". Jobright's own copy
    # of the posting names the role and employer best; the fetched page next.
    src_title, src_company = postings.source_meta(job.url)
    # A role found by Scout carries the board's own title; an application page's
    # <title> can be "See Job Post" (Cross River, 2026-10-22), so it does not win.
    title = (job.title if job.source in ("scout", "lennys") and job.title else "") \
        or src_title or posting.title or job.title
    # Last: the title and the URL themselves (`company_from`). An ATS embed
    # exposes no company at all, and naming the folder after the job's own
    # title is better than naming fifteen of them `unknown-company`.
    company = (job.company or src_company or posting.company
               or company_from(title, job.url))
    if (title, company) != (job.title, job.company):
        job = queue.update(job.id, title=title, company=company) or job

    if job.resume_base:
        try:
            base = resume_bases.get(job.resume_base)
            choice = resume_bases.Choice(
                base.id, job.resume_base_source or "sticky",
                job.resume_base_confidence or "high",
                job.resume_base_reason or "Kept this job's selected resume.",
            )
        except KeyError:
            choice = resume_bases.choose(posting)
            base = resume_bases.get(choice.base)
    else:
        choice = resume_bases.choose(posting)
        base = resume_bases.get(choice.base)
    job = queue.update(
        job.id,
        resume_base=choice.base,
        resume_base_source=choice.source,
        resume_base_confidence=choice.confidence,
        resume_base_reason=choice.reason,
    ) or job

    app_dir = allocate(job)
    store.write(app_dir, "posting.md", posting.to_markdown())
    write_screen(app_dir, job, posting)

    stories = pick_stories(app_dir, posting)
    try:
        result, note = write_resume(
            app_dir, posting, stories, extra_instruction, model=job.tailor_model,
            base=base, choice=choice,
        )
    except tailor.Mismatch as exc:
        # A poor-fit verdict is advice, not a decision. The job still stops at
        # checkpoint 1 with the reasoning on show; only the human closes it.
        store.write(
            app_dir,
            "mismatch.md",
            f"# The model thinks this is a poor fit\n\n{exc}\n\n"
            "Nothing was tailored. Reject it if you agree, or re-tailor with a "
            "note if you think the model is wrong.\n",
        )
        store.set_status(app_dir, held or Status.AWAITING_REVIEW, f"poor fit: {exc}")
        if not keep_status or revive:
            queue.update(job.id, status=Status.AWAITING_REVIEW)
        print(f"  flagged as a poor fit, waiting on you: {exc}")
        tell_tab(job, "done", "Poor fit, says the model; decide on the review page")
        return app_dir

    if os.environ.get(COVER_ON_DEMAND, "1") == "0":
        note += "; " + write_cover_letter(app_dir, posting, result.tex, extra_instruction, stories,
                                          model=job.tailor_model)
    else:
        # Written by the fill when the form has a slot for one, or by the
        # review page's button; most forms never ask (2026-10-24).
        note += "; cover letter when a form asks for one"

    store.set_status(app_dir, held or Status.AWAITING_REVIEW, note)
    if keep_status:
        # Documents only: the job keeps the standing it had, and the fill
        # is not touched. The new resume is for the human to hand over.
        if revive:
            queue.update(job.id, status=Status.AWAITING_REVIEW)
            print(f"  re-tailored after a failure, back at checkpoint 1: {app_dir}")
            return app_dir
        print(f"  re-tailored, status left at {held.value if held else 'unchanged'}: {app_dir}")
        return app_dir
    queue.update(job.id, status=Status.AWAITING_REVIEW)
    if not auto_approve(app_dir, job):
        tell_tab(job, "done", "Tailored; approve it on the review page")
    prepare_outreach(job)
    return app_dir


def prepare_outreach(job: Job) -> None:
    """Lenny's Jobs finds come with their company's people and a draft per
    role, so the person only reads and approves. Drafts only: this module
    has no way to send, and a failure here never touches the job."""
    if job.source != "lennys" or not settings.auto_outreach():
        return
    try:
        from outreach import drafting
        print(f"  {drafting.prepare(job.id)}")
    except Exception as exc:  # noqa: BLE001 - the extra must not fail the application
        print(f"  outreach failed: {str(exc)[:160]}")


def auto_approve(app_dir: Path, job: Job) -> bool:
    """Checkpoint 1 without a click, when the job's auto-approve box (the
    banner, at capture) or failing that the `auto_fill` switch says so, and
    the screen did not reject. The clean verdict is what queued the job in
    the first place, so the fill starts as soon as the documents exist and
    the human decides once, on the filled form. Never for a poor-fit verdict
    (that returns before this) or a `reject` screen. The fill itself still
    stops at checkpoint 2; nothing submits."""
    wanted = settings.auto_fill() if job.auto_fill is None else bool(job.auto_fill)
    if not wanted:
        return False
    try:
        verdict = json.loads((app_dir / "screen.json").read_text()).get("verdict")
    except (OSError, ValueError):
        verdict = None
    if verdict == "reject":
        tell_tab(job, "done", "The screen says reject; decide on the review page")
        return False
    fit = qualify.load(app_dir) or {}
    if fit.get("verdict") == "not_qualified":
        tell_tab(job, "done", "Not qualified, by the brief; decide on the review page")
        return False
    # The bullet review is a gate, not a score: anything unverified, put back
    # to the master's wording, or not reviewed at all waits for the person.
    import os
    if os.environ.get("AUTOPILOT_BULLET_REVIEW", "1") != "0":
        checked = reviewer.load(app_dir)
        if checked is None or checked.needs_human:
            tell_tab(job, "done", "The bullet review needs you; decide on the review page")
            return False
    store.set_status(app_dir, Status.APPROVED, "approved by autopilot: clean screen, tailored")
    queue.update(job.id, status=Status.APPROVED)
    pid = runner.start_fill(job.id)
    if pid:
        store.set_status(app_dir, Status.APPROVED, f"filling started (pid {pid})")
        tell_tab(job, "working", "Tailored; the browser agent is taking over")
        print(f"  approved by autopilot, fill started (pid {pid})")
    else:
        tell_tab(job, "done", "Tailored; start the fill on the review page")
        print("  approved by autopilot; the fill did not start (autofill off or already running)")
    return True


def tell_tab(job: Job, state: str, note: str) -> None:
    """Set the banner on the job's open tab, so the human sees the pipeline
    working between Jobright's autofill and the browser agent. Advice only:
    no tab, no browser, no harm."""
    try:
        autofill.notify_url(chrome.cdp_url(chrome.port()), job.url, state, note)
    except Exception as error:  # noqa: BLE001 - the indicator is advice
        print(f"  banner: {error}", file=sys.stderr)


def write_marked(app_dir: Path, original: str, tailored: str) -> None:
    """The tailored resume with its changes coloured, for the review page's
    "changes" and "before and after" views. Derived from the source that
    already compiled, so a failure here is a missing view, not a failed run."""
    for mode in mark.MODES:
        name = f"resume_{mode}"
        try:
            store.write(app_dir, f"{name}.tex", mark.mark(original, tailored, mode))
            texc.compile_pdf(app_dir / f"{name}.tex", app_dir / f"{name}.pdf")
        except Exception as error:  # noqa: BLE001 - a view, never the run
            print(f"{name}: {error}")


def write_screen(app_dir: Path, job: Job, posting) -> None:
    """Archive the auto-reject screen next to the posting.

    The extension usually screened this URL when the page was opened, so this
    is a cache hit; otherwise the model runs once here. Not fatal: a screen
    that failed is a line on the review page, not a reason to skip tailoring.
    """
    try:
        result, _ = screen_server.screen_url(job.url, posting.text, title=posting.title)
        store.write(app_dir, "screen.json", json.dumps(result.to_dict(), indent=2) + "\n")
        if result.flags:
            print(f"  screen: {result.verdict}, {len(result.flags)} flag(s)")
    except Exception as exc:  # noqa: BLE001 - see docstring
        store.write(app_dir, "screen_error.txt", f"{type(exc).__name__}: {exc}\n")
        print(f"  screen failed: {exc}", file=sys.stderr)


def pick_stories(app_dir: Path, posting) -> list[str]:
    """Which experience documents this posting gets, recorded in the folder
    so the cover letter and the form answers reuse the same pick and the
    review page can show it. A failed pick is no stories, not a failed job."""
    try:
        stories = profile.pick(posting.text)
    except Exception as exc:  # noqa: BLE001 - advice, not a step
        print(f"  story pick failed: {exc}", file=sys.stderr)
        stories = []
    store.write(app_dir, "stories_used.txt", "\n".join(stories) + ("\n" if stories else ""))
    if stories:
        print(f"  stories: {', '.join(stories)}")
    return stories


RESUME_ROUNDS = 1   # each revision is a full tailoring pass with compiles; one is the budget


def review_resume(app_dir: Path, posting, master: str, result, brief, profile_text: str,
                  base, model: Optional[str], options: str = ""):
    """Judge the tailored resume against the brief and revise it once while
    it improves (tailor.critic). The revision starts from the tailored resume
    and goes through every check tailoring has (lines, counts, links,
    invented figures, the page count), so a revision that breaks one is
    never kept. The diff stays against the master."""
    revised: dict = {}

    def revise(tex: str, fixes: list[str]) -> str:
        again = tailor.tailor(
            posting, resume_tex=tex, page_check=make_page_check(base.tex_path),
            target_pages=base_page_count(base.tex_path, base.pdf_path),
            extra_instruction="An editor reviewed this tailored resume. Apply these notes:\n"
                              + "\n".join(f"- {fix}" for fix in fixes),
            profile=profile_text, model=model, layout_pdf=base.pdf_path,
            brief=brief.as_message() if brief else "", revision=True, options=options)
        revised[again.tex] = again
        return again.tex

    review = critic.refine("resume", result.tex, brief, revise, rounds=RESUME_ROUNDS,
                           # One line per bullet, so the invention check can name the one it doubts.
                           body_of=lambda tex: "\n".join(" ".join(tailor.layout.visible(b).split())
                                                         for b in tailor.bullets(tex)),
                           sources={"resume": master, "stories": profile_text})
    store.write(app_dir, "resume_review.json", json.dumps(review.to_dict(), indent=2) + "\n")
    if review.text == result.tex:
        return result
    kept = revised[review.text]
    kept.diff = tailor.make_diff(master, kept.tex)
    kept.suggestions = (result.suggestions + "\n\n**Revised after review** "
                        f"({review.first_score} to {review.score}/10): "
                        + "; ".join(review.trace[0].get("fixes", [])[:3]))
    kept.rejections = result.rejections + kept.rejections
    kept.warnings = result.warnings + kept.warnings
    kept.attempts = result.attempts
    return kept


BULLET_ROUNDS = 2   # corrections the writer gets for flagged bullets before the master's wording goes back


def _story_texts(slugs: list[str]) -> dict[str, str]:
    out = {}
    for slug in dict.fromkeys(slugs):
        doc = profile.STORIES / slug / "tailor.md"
        if doc.exists():
            out[slug] = doc.read_text(errors="replace")
    return out


def verify_bullets(app_dir: Path, posting, master: str, result, brief, profile_text: str,
                   stories: list[str], base, model: Optional[str], options: str = ""):
    """The ML/DS technical review of every rewritten bullet against its own
    source (tailor.reviewer), with a bounded, controlled revision:

    1. bullets the review flags (FAIL or WARN) go back to the writer with the
       issue and the evidence, never a replacement fact;
    2. the correction passes every tailoring check (lines, counts, links,
       figures, pages) and is reviewed again against the same sources;
    3. it is kept only when no bullet got worse and fewer are flagged;
    4. after BULLET_ROUNDS, a bullet still flagged gets its master wording
       back when it has one (a story swap does not: its master bullet belongs
       to another entry), and the job waits for the person.

    The writing-quality score never enters into it. The trace is saved as
    `bullet_review.json`. Off with AUTOPILOT_BULLET_REVIEW=0."""
    import os
    if os.environ.get("AUTOPILOT_BULLET_REVIEW", "1") == "0":
        return result
    requirements = [r.text for r in brief.requirements] if brief else []
    texts = _story_texts(stories)
    current = result
    review = reviewer.review(master, current.tex, requirements, texts)
    for n in range(1, BULLET_ROUNDS + 1):
        flagged = [b for b in review.bullets if b.verdict in (reviewer.FAIL, reviewer.WARN)]
        if not flagged or review.error:
            break
        notes = reviewer.revision_notes(review)
        entry = {"round": n, "asked": [{"bullet": b.rewritten, "issue": b.issue, "evidence": b.evidence}
                                       for b in flagged]}
        try:
            again = tailor.tailor(
                posting, resume_tex=current.tex, page_check=make_page_check(base.tex_path),
                target_pages=base_page_count(base.tex_path, base.pdf_path),
                extra_instruction="A technical reviewer checked each rewritten bullet against the only text "
                                  "that supports it. Fix exactly these, and change nothing else:\n"
                                  + "\n".join(f"- {note}" for note in notes),
                profile=profile_text, model=model, layout_pdf=base.pdf_path,
                brief=brief.as_message() if brief else "", revision=True, options=options)
        except Exception as exc:  # noqa: BLE001 - the correction failed a tailoring check
            entry["outcome"] = f"correction refused by the checks: {str(exc)[:200]}"
            review.rounds.append(entry)
            break
        checked = reviewer.review(master, again.tex, requirements, texts)
        before, after = review.counts, checked.counts
        worse = after[reviewer.FAIL] > before[reviewer.FAIL] or after[reviewer.UNKNOWN] > before[reviewer.UNKNOWN]
        fewer = (after[reviewer.FAIL] + after[reviewer.WARN]) < (before[reviewer.FAIL] + before[reviewer.WARN])
        by_original = {b.original: b for b in checked.bullets if b.original}
        for b in flagged:
            fixed = by_original.get(b.original)
            if fixed:
                b.correction, b.correction_verdict = fixed.rewritten, fixed.verdict
        entry["counts_before"], entry["counts_after"] = before, after
        if worse or not fewer:
            entry["outcome"] = "correction not kept: " + ("a bullet got worse" if worse else "nothing improved")
            review.rounds.append(entry)
            break
        entry["outcome"] = "correction kept"
        checked.rounds = review.rounds + [entry]
        again.diff = tailor.make_diff(master, again.tex)
        again.rejections = current.rejections + again.rejections
        again.warnings = current.warnings + again.warnings
        again.attempts = current.attempts
        again.suggestions = current.suggestions
        current, review = again, checked
    # Whatever is still flagged: the master's own wording where there is one.
    safe = [b.index for b in review.bullets
            if b.verdict in (reviewer.FAIL, reviewer.WARN) and b.source_kind.startswith("master") and b.original_tex]
    if safe:
        restored = reviewer.revert(current.tex, review, safe)
        if restored != current.tex:
            try:
                tailor._check_invented(master, restored, profile_text, posting.text)
                current.tex = restored
                current.diff = tailor.make_diff(master, restored)
                review.reverted = safe
                for b in review.bullets:
                    if b.index in safe:
                        b.correction, b.correction_verdict = b.original, "master wording restored"
            except Exception as exc:  # noqa: BLE001 - keep the flagged bullet; the person decides
                review.rounds.append({"round": "revert", "outcome": f"could not restore: {str(exc)[:200]}"})
    # The person's own edits for this job win over every model's wording.
    try:
        job_id = json.loads((app_dir / "job.json").read_text()).get("id", "")
    except (OSError, ValueError):
        job_id = ""
    if job_id:
        held, changed = reviewer.apply_locks(current.tex, review, job_id)
        if changed:
            current.tex, current.diff = held, tailor.make_diff(master, held)
            for b in review.bullets:
                if b.index in changed:
                    b.correction, b.correction_verdict, b.decision = "", "your edit, kept", "edit"
                    b.edited = next(r["text"] for r in reviewer.locks(job_id) if r["original"] == b.original)
            review.rounds.append({"round": "locks", "outcome": f"{len(changed)} of your edits put back"})
    reviewer.save(app_dir, review)
    counts = review.counts
    current.suggestions = (current.suggestions + "\n\n**Bullet review** "
                           f"(PASS {counts['PASS']}, WARN {counts['WARN']}, FAIL {counts['FAIL']}, "
                           f"UNKNOWN {counts['UNKNOWN']}"
                           + (f"; {len(review.reverted)} put back to the master's wording" if review.reverted else "")
                           + ")" + (": waits for you" if review.needs_human else ""))
    return current


def write_brief(app_dir: Path, posting, master: str, profile_text: str) -> Optional[briefs.Brief]:
    """What the job asks for and the candidate's evidence for each, read once
    and given to every document. Not fatal: without it the writers run as
    they did before, and the reason is kept next to the documents."""
    try:
        brief = briefs.build(posting.text, master, profile_text)
    except Exception as exc:  # noqa: BLE001 - see docstring
        store.write(app_dir, "brief_error.txt", f"{type(exc).__name__}: {exc}\n")
        print(f"  brief failed: {exc}", file=sys.stderr)
        return None
    store.write(app_dir, briefs.FILE, json.dumps(brief.to_dict(), indent=2) + "\n")
    gaps = sum(1 for r in brief.musts() if r.gap)
    print(f"  brief: {len(brief.requirements)} requirements, {gaps} required without evidence")
    return brief


def write_bullets(app_dir: Path, brief, master: str) -> tuple[str, list[str]]:
    """Bullets for every job on the resume, for this posting, from the
    bullet bank first and fresh drafts where it is short (tailor.bank).
    The master is never changed; the tailoring gets them as options.
    Not fatal. Returns (the tailoring's section, the stories they came from)."""
    try:
        result = bank.for_job(brief, master, job_key=app_dir.name)
    except Exception as exc:  # noqa: BLE001 - see docstring
        store.write(app_dir, "bullets_error.txt", f"{type(exc).__name__}: {exc}\n")
        print(f"  bullet bank failed: {exc}", file=sys.stderr)
        return "", []
    store.write(app_dir, "bullets.json", json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    reused = sum(len(j["reused"]) for j in result["jobs"])
    banked = sum(1 for j in result["jobs"] for b in j["drafted"] if b.get("banked"))
    print(f"  bullets: {reused} from the bank, {banked} new into it")
    return bank.options(result), result.get("stories", [])


def write_qualify(app_dir: Path, brief, master: str, profile_text: str) -> None:
    """Am I qualified, and why not: the verdict the review card leads with.
    Not fatal, and it never skips the job; a not-qualified job is still
    tailored and waits for the person instead of approving itself."""
    try:
        screen = json.loads((app_dir / "screen.json").read_text()) if (app_dir / "screen.json").exists() else None
        assessment = qualify.assess(brief, master, profile_text, screen)
    except Exception as exc:  # noqa: BLE001 - see docstring
        store.write(app_dir, "qualify_error.txt", f"{type(exc).__name__}: {exc}\n")
        print(f"  qualification failed: {exc}", file=sys.stderr)
        return
    store.write(app_dir, qualify.FILE, json.dumps(assessment.to_dict(), indent=2) + "\n")
    print(f"  {assessment.summary}")


def write_cover_letter(app_dir: Path, posting, resume_tex: str, extra_instruction: str = "",
                       stories: Optional[list[str]] = None, model: Optional[str] = None) -> str:
    """Write the letter next to the resume. Returns a one-line note for the status.

    Deliberately not fatal: the resume it sits beside cost several model
    round-trips and a compile, and a letter that failed to come out is a
    reason to look at cover_letter_error.txt, not to throw that away. The
    review page shows the letter when it exists and the error when it does
    not.
    """
    try:
        brief = briefs.load(app_dir)
        brief_text = brief.as_message() if brief else ""
        profile_text = tailor.load_profile(stories=stories)
        letter = cover.write(posting, resume_tex, profile_text,
                             extra_instruction, model=model, brief=brief_text)
        # Judged against the brief and revised while it improves (tailor.critic).
        # The review is kept next to the letter; a failed judge leaves the letter as written.
        review = critic.refine(
            "cover", letter.text, brief,
            revise=lambda text, fixes: cover.revise(posting, resume_tex, profile_text, text, fixes,
                                                    model=model, brief=brief_text),
            check=cover.problems,
            # The posting backs what the letter says about the company: without it,
            # true lines about Glean read as invented and capped the letter at 3/10.
            sources={"resume": resume_tex, "stories": profile_text, "posting": posting.text},
        )
        letter.text = review.text
        store.write(app_dir, "cover_review.json", json.dumps(review.to_dict(), indent=2) + "\n")
        store.write(app_dir, "cover_letter.md", letter.text + "\n")
        store.write(app_dir, "cover_letter.tex", cover.to_tex(letter.text))
        texc.compile_pdf(app_dir / "cover_letter.tex", app_dir / "cover_letter.pdf")
    except Exception as exc:  # noqa: BLE001 - see docstring
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, texc.CompileError) and exc.log:
            detail += "\n\n" + exc.tail(15)
        store.write(app_dir, "cover_letter_error.txt", detail + "\n")
        print(f"  cover letter failed: {detail.splitlines()[0]}", file=sys.stderr)
        return "no cover letter (see cover_letter_error.txt)"
    scored = f", scored {review.score}/10" + (f" (from {review.first_score})" if review.improved else "") \
        if review.score else ""
    return f"cover letter {letter.words} words{scored}"


REGENERATE_TARGETS = {"resume", "cover"}
COVER_ON_DEMAND = "AUTOPILOT_COVER_ON_DEMAND"   # "0" writes the letter with the resume, as before


def _archived_posting(app_dir: Path, job: Job) -> fetch.Posting:
    """Rebuild the model input from the posting already archived for this job."""
    path = app_dir / "posting.md"
    if not path.exists():
        raise FileNotFoundError(f"{path.name} is missing")
    saved = path.read_text(errors="replace")
    marker = "\n---\n"
    text = saved.split(marker, 1)[1].strip() if marker in saved else saved
    return fetch.Posting(url=job.url, text=text, title=job.title, company=job.company)


def _carry(source: Path, destination: Path, names: list[str]) -> None:
    """Copy immutable artifacts into a new archive version."""
    for name in names:
        path = source / name
        if path.is_file():
            store.write(destination, name, path.read_bytes())


def _carry_assets(source: Path, destination: Path) -> None:
    for path in source.iterdir():
        if path.is_file() and path.suffix in (".cls", ".sty"):
            store.write(destination, path.name, path.read_bytes())


def _archived_base(source: Path, job: Job) -> tuple[resume_bases.ResumeBase, resume_bases.Choice]:
    value = {}
    path = source / "resume_base.json"
    if path.exists():
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            value = {}
    base_id = str(value.get("base") or job.resume_base or resume_bases.primary_id())
    try:
        base = resume_bases.get(base_id)
    except KeyError:
        base = resume_bases.get()
    choice = resume_bases.Choice(
        base.id,
        str(value.get("source") or job.resume_base_source or "sticky"),
        str(value.get("confidence") or job.resume_base_confidence or "high"),
        str(value.get("reason") or job.resume_base_reason
            or "Kept this job's selected resume."),
        str(value.get("model") or ""),
        bool(value.get("cached", False)),
    )
    return base, choice


def regenerate(job: Job, instruction: str, target: str) -> Path:
    """Regenerate one document while carrying the other into a new version.

    The current archive remains untouched. The queue points at the new folder
    only after the requested document succeeds, and the job keeps its current
    status, so regenerating cannot approve, fill, submit, or reopen anything.
    """
    if target not in REGENERATE_TARGETS:
        raise ValueError(f"no such regeneration target {target!r}")
    current = queue.get(job.id) or job
    if not current.app_dir:
        raise FileNotFoundError("this job has no application folder")
    source = Path(current.app_dir)
    if not source.exists():
        raise FileNotFoundError(f"application folder {source.name} is gone")

    posting = _archived_posting(source, current)
    stories = profile.read_used(source)
    base, choice = _archived_base(source, current)
    app_dir = store.create(current)
    store.set_status(app_dir, Status.TAILORING, f"regenerating {target}")
    _carry(source, app_dir, ["posting.md", "screen.json", "screen_error.txt"])
    resume_bases.copy_assets(base, app_dir)
    _carry_assets(source, app_dir)
    store.write(app_dir, "stories_used.txt", "\n".join(stories) + ("\n" if stories else ""))

    try:
        if target == "resume":
            _, note = write_resume(
                app_dir, posting, stories, instruction, model=current.tailor_model,
                base=base, choice=choice,
            )
            _carry(source, app_dir, [
                "cover_letter.md", "cover_letter.tex", "cover_letter.pdf",
                "cover_letter_error.txt",
            ])
        else:
            _carry(source, app_dir, [
                "resume.tex", "resume.pdf", "resume.diff", "suggestions.md",
                "resume_changes.tex", "resume_changes.pdf",
                "resume_both.tex", "resume_both.pdf", "mismatch.md",
                "resume_base.json", "master_resume.tex", "master_resume.pdf",
            ])
            resume_tex = (source / "resume.tex").read_text()
            note = write_cover_letter(
                app_dir, posting, resume_tex, instruction, stories, model=current.tailor_model,
            )
            if not (app_dir / "cover_letter.md").exists():
                detail = (app_dir / "cover_letter_error.txt").read_text(errors="replace").strip()
                raise cover.CoverLetterError(detail or "cover letter regeneration failed")
    except Exception as exc:
        store.set_status(app_dir, Status.FAILED, f"{type(exc).__name__}: {exc}")
        raise

    store.set_status(app_dir, current.status, f"regenerated {target}; {note}")
    queue.update(current.id, app_dir=str(app_dir), status=current.status, error=None)
    return app_dir


def save_cover(job: Job, text: str) -> Path:
    """Archive a human-reviewed cover-letter edit as a new document version."""
    text = cover.clean(text)
    issues = cover.problems(text)
    if issues:
        raise cover.CoverLetterError("; ".join(issues))
    current = queue.get(job.id) or job
    if not current.app_dir:
        raise FileNotFoundError("this job has no application folder")
    source = Path(current.app_dir)
    if not source.exists():
        raise FileNotFoundError(f"application folder {source.name} is gone")

    stories = profile.read_used(source)
    base, _ = _archived_base(source, current)
    app_dir = store.create(current)
    store.set_status(app_dir, Status.TAILORING, "saving edited cover letter")
    resume_bases.copy_assets(base, app_dir)
    _carry_assets(source, app_dir)
    _carry(source, app_dir, [
        "posting.md", "screen.json", "screen_error.txt", "resume.tex", "resume.pdf",
        "resume.diff", "suggestions.md", "resume_changes.tex", "resume_changes.pdf",
        "resume_both.tex", "resume_both.pdf", "mismatch.md",
        "resume_base.json", "master_resume.tex", "master_resume.pdf",
    ])
    store.write(app_dir, "stories_used.txt", "\n".join(stories) + ("\n" if stories else ""))
    try:
        store.write(app_dir, "cover_letter.md", text + "\n")
        store.write(app_dir, "cover_letter.tex", cover.to_tex(text))
        texc.compile_pdf(app_dir / "cover_letter.tex", app_dir / "cover_letter.pdf")
    except Exception as exc:
        store.set_status(app_dir, Status.FAILED, f"{type(exc).__name__}: {exc}")
        raise

    store.set_status(app_dir, current.status, f"saved edited cover letter; {len(text.split())} words")
    queue.update(current.id, app_dir=str(app_dir), status=current.status, error=None)
    return app_dir


def save_resume(job: Job, tex: str) -> Path:
    """Validate and archive a human-reviewed resume edit as a new version."""
    current = queue.get(job.id) or job
    if not current.app_dir:
        raise FileNotFoundError("this job has no application folder")
    source = Path(current.app_dir)
    if not source.exists():
        raise FileNotFoundError(f"application folder {source.name} is gone")

    base, _ = _archived_base(source, current)
    master_path = source / "master_resume.tex"
    master = master_path.read_text() if master_path.exists() else base.tex_path.read_text()
    master_pdf = source / "master_resume.pdf"
    width = layout.chars_per_line(master_pdf if master_pdf.exists() else base.pdf_path)
    posting = _archived_posting(source, current)
    stories = profile.read_used(source)
    profile_text = tailor.load_profile(stories=stories)
    warnings = tailor._validate(master, tex, profile_text, posting.text, width)

    app_dir = store.create(current)
    store.set_status(app_dir, Status.TAILORING, "saving edited resume")
    resume_bases.copy_assets(base, app_dir)
    _carry_assets(source, app_dir)
    _carry(source, app_dir, [
        "posting.md", "screen.json", "screen_error.txt", "cover_letter.md",
        "cover_letter.tex", "cover_letter.pdf", "cover_letter_error.txt",
        "resume_base.json", "master_resume.tex", "master_resume.pdf",
    ])
    store.write(app_dir, "stories_used.txt", "\n".join(stories) + ("\n" if stories else ""))
    try:
        store.write(app_dir, "resume.tex", tex)
        store.write(app_dir, "resume.diff", tailor.make_diff(master, tex) or "(no changes)\n")
        store.write(
            app_dir,
            "suggestions.md",
            "# Resume edit\n\nChanged from highlighted text on the review page."
            + (f"\n\n**Length warnings:** {'; '.join(warnings)}" if warnings else "")
            + "\n",
        )
        pdf = texc.compile_pdf(app_dir / "resume.tex", app_dir / "resume.pdf")
        pages = texc.page_count(pdf)
        target = base_page_count(
            master_path if master_path.exists() else base.tex_path,
            master_pdf if master_pdf.exists() else base.pdf_path,
        )
        if pages is None:
            raise tailor.TailorError("could not verify the edited resume's page count")
        if pages != target:
            raise tailor.TailorError(f"compiled to {pages} pages, must be {target}")
        write_marked(app_dir, master, tex)
    except Exception as exc:
        store.set_status(app_dir, Status.FAILED, f"{type(exc).__name__}: {exc}")
        raise

    store.set_status(app_dir, current.status, f"saved edited resume; {pages} page(s)")
    queue.update(current.id, app_dir=str(app_dir), status=current.status, error=None)
    return app_dir


def process_safely(job: Job) -> bool:
    try:
        app_dir = process(job)
    except Exception as exc:  # noqa: BLE001 - one bad job must not stop the batch
        detail = f"{type(exc).__name__}: {exc}"
        print(f"  failed: {detail}", file=sys.stderr)
        if isinstance(exc, texc.CompileError) and exc.log:
            print(exc.tail(15), file=sys.stderr)
        queue.update(job.id, status=Status.FAILED, error=detail)
        tell_tab(job, "error", f"Tailoring failed: {detail.splitlines()[0][:80]}")
        existing = queue.get(job.id)
        if existing and existing.app_dir:
            app_dir = Path(existing.app_dir)
            store.set_status(app_dir, Status.FAILED, detail)
            if not (app_dir / "error.txt").exists():
                store.write(app_dir, "error.txt", detail + "\n\n" + traceback.format_exc())
            # Keep what the model actually produced. A rejection that only
            # appears on a later retry cannot be diagnosed from its reason.
            for record in getattr(exc, "attempts", []) or []:
                name = f"rejected_attempt_{record['attempt']}.tex"
                if not (app_dir / name).exists():
                    store.write(
                        app_dir, name,
                        f"% rejected: {record['reason']}\n{record['tex']}",
                    )
        return False
    print(f"  ready for review: {app_dir}")
    return True


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        job = queue.get(argv[1])
        if job is None:
            print(f"unknown job id {argv[1]}", file=sys.stderr)
            return 1
        jobs = [job]
    else:
        jobs = queue.pending()

    if not jobs:
        print("nothing queued")
        return 0

    failures = 0
    for job in jobs:
        print(f"{job.id}  {job.title or job.url}")
        if not process_safely(job):
            failures += 1

    print(f"\n{len(jobs) - failures}/{len(jobs)} ready for review at http://127.0.0.1:8787")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
