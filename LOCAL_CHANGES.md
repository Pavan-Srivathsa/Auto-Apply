## 2026-09-28 — Restore highlighted resume previews
Asked: "Fix it then."
Changed: `tex/mark.py` and `tests/test_mark.py`.
Intent: The Changes and Before-and-after resume PDFs compile even when the master resume does not already load a colour package.

## 2026-09-29 — Choose how each application is filled
Asked: "I need another button here" to inspect the employer form, then choose Autopilot or Jobright followed by a correction pass.
Changed: `review/index.html`, `server/review.py`, `server/runner.py`, `browser/fill.py`, `browser/forms/engine.py`, and their tests.
Intent: An approved job offers Open application, Autopilot fill, and Jobright + check. The check is deterministic from saved profile facts/corrections, never model judgement, and protected fields remain untouched.

## 2026-09-29 — Keep the form handoff visible
Asked: "After Jobright autofill is done ... make sure [the bar] stays."
Changed: `capture/content.js` and `tests/test_capture.py`.
Intent: Once any automation works on an application form, the full Autopilot bar remains expanded through the handoff to the human; only an explicit minimize click folds it.

## 2026-10-12 — Generate evidence-led personalized cover letters
Asked: replace generic cover letters with a detailed personalization, evidence, company-fit, and quality-control prompt.
Changed: the personal `cover` and `cover.format` prompt overrides, `tailor/cover.py`, and `tests/test_cover.py`.
Intent: Letters now make one role-specific technical argument in 250–350 words, use only supported facts and posting-provided company details, and close with the requested “Best,” sign-off.

## 2026-10-12 — Repair rejection and regenerate one document
Asked: fix the Reject/Cancel controls and allow the resume or cover letter to be regenerated with a specific change prompt.
Changed: `review/index.html`, `server/review.py`, `server/thread.py`, `pipeline.py`, and focused tests.
Intent: Rejection uses explicit dialog actions and stops that job’s active fill, while the thread can regenerate only the resume, only the cover letter, or both; each request accepts optional guidance, writes a new immutable version, and preserves the job’s status and untouched document.

## 2026-10-12 — Permanently delete a job
Asked: add an option to delete the job and all of its archived versions.
Changed: `review/index.html`, `server/review.py`, `server/queue.py`, `server/thread.py`, `archive/store.py`, and review tests.
Intent: A double-confirmed Delete stops that job’s fill, removes its queue row, every generated application folder, archive index rows, and its conversation; it refuses deletion while tailoring or document regeneration is still running.
