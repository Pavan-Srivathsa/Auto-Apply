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

## 2026-10-02 — Human-approved recruiter outreach
Asked: implement the Automated Outreach System plan, with Apollo first, Jobright fallback, a carousel after the cover letter, and Gmail delivery.
Changed: `outreach/`, `server/outreach.py`, `tailor/outreach.py`, `tailor/outreach_rules.md`, `tailor/prompts.py`, `mailing.py`, `scout/mail.py`, `review/index.html`, configuration, and tests.
Intent: Each job can find recruiter and manager contacts, prepare independently editable carousel drafts, and send a tailored resume only after one explicit human approval. No pipeline, browser agent, or prompt can send email.

## 2026-10-02 — Surface Outreach save failures
Asked: "I have added the key and the gmail password ... but no response."
Changed: `review/index.html`.
Intent: A failed Apollo/Gmail save, empty fields, or a stale server now alerts instead of looking like the button did nothing.

## 2026-10-02 — Select people, then write every email
Asked: select the people to email, one write action, carousel shows them together.
Changed: `server/outreach.py`, `review/index.html`, `tests/test_outreach.py`.
Intent: Tick the contacts, press Write emails once, and the carousel holds every draft. Nothing is sent until each slide is approved.

## 2026-10-02 — Apollo search keeps locked people
Asked: "its done. lets test"
Changed: `outreach/apollo.py`, `outreach/models.py`, `tests/test_outreach.py`.
Intent: Apollo's free people search returns first names and ids, not LinkedIn URLs. Those rows are listed now, and enrichment uses the Apollo id.

## 2026-10-02 — Keep the review server alive
Asked: "This site can’t be reached … ERR_CONNECTION_REFUSED"
Changed: `scripts/autopilot`.
Intent: The review server detaches into its own session so closing the launcher no longer kills port 8787.

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

## 2026-10-12 — Rewrite highlighted cover-letter text
Asked: make cover letters warmer, and allow highlighted passages to be changed with a chosen model whose configured name appears on hover.
Changed: the personal cover prompt, `tailor/cover.py`, `tailor/prompts.py`, `pipeline.py`, `server/review.py`, `review/index.html`, and focused tests.
Intent: Cover letters avoid presumptive claims about growing the business; the review page pairs the PDF with selectable text, offers Default or Opus for an instructed rewrite, previews the replacement, and saves only after a human click into a new immutable version.

## 2026-10-12 — Rewrite highlighted resume text
Asked: add the same highlight-and-model editing option to the resume.
Changed: `tailor/tailor.py`, `tailor/prompts.py`, `pipeline.py`, `server/review.py`, `review/index.html`, and focused tests.
Intent: The review page pairs the resume PDF with editable source, offers Default or Opus for an instructed selection rewrite with model names on hover, previews every replacement, and creates a new immutable version only after the edited resume passes validation and compilation.

## 2026-10-12 — Route each job to its best resume
Asked: support two or more resume formats and have an agent choose the best starting resume for each position before tailoring.
Changed: `tailor/resume_bases.py`, the pipeline, setup and review APIs, the posting banner, the Resumes and Jobs views, prompt catalog, job model, layout and profile handling, and focused tests.
Intent: Any number of independently formatted LaTeX resumes can be compiled, profiled once, archived, restored, and selected manually. Each new job uses an exact-role rule, a cached match, or one cheap routing call to choose one resume, then tailors only that source. The choice and historical master are archived with every immutable document version.

## 2026-10-12 — Permanently remove an unused resume
Asked: add a delete button for uploaded resume formats.
Changed: the Resumes tab, setup API, resume registry, and focused tests.
Intent: An archived resume can be permanently deleted after confirmation, while active resumes and resumes still selected by a job are protected from deletion.

## 2026-10-21 — Lenny's Jobs as a Scout source, with outreach drafts
Asked: "open jobs on lennyjobs and automatically apply for the jobs there and also reach out to people from that particular company and apply"
Changed: `scout/lennys.py` (new), `scout/__init__.py` (`Posting.company`, `detect`), `scout/providers.py`, `scout/run.py`, `browser/autofill.py` (`attached(background=)`), `server/outreach.py` (`prepare`), `server/settings.py` (`auto_outreach`), `pipeline.py` (`prepare_outreach`), and `tests/test_lennys.py`.
Intent: Paste `https://www.lennysjobs.com/` into Scout. The page is read in a background tab of the Autopilot Chrome (its guest search API answers 401) with Engineering (Software), entry and internship levels and Past week set; each row's employer link is queued under its own company with source `lennys`, and the usual fill stops at the completed form for the human's Submit. Once tailoring is done, a `lennys` job gets one recruiter and one hiring-manager draft through Apollo. Nothing is sent: each draft still needs its approval click on the review page.
Added the same day, after a guest proved capped at 50 rows (a paid-subscription gate): each Lenny's row whose employer link is a board Scout reads (Greenhouse, Ashby, Lever, BambooHR, SmartRecruiters, Workday) gets its own watch, verified before it is created, at most 15 per check, marked `via: lennys` so its jobs get the outreach drafts too; `follow: false` in the Lenny's watch args turns it off.

## 2026-10-21 — Jobs stay in date order; only projects move
Asked: "keep the jobs in date order. Only project around. Now keep it separate from the loop engineering part."
Changed: `tailor/tailor.py` (`roles`, `_check_role_order` in `_validate`), `tests/test_tailor.py`, and the person's own `tailor` prompt edit (two passages: entry order applies to PROJECTS only; jobs keep the master's order), saved through `tailor.prompts.save` so it merges with future stock updates.
Intent: A resume that moves a job above a newer one is sent back with the order named, like every other tailoring check; bullets inside a job and PROJECTS entries may still move. Committed on `mine`, apart from the `loop-engineering` branch.

## 2026-10-22 — Greenhouse postings read from the board API
Asked: testing the loop on the Stripe job ("Use stripe").
Changed: `tailor/fetch.py` (`greenhouse_job`, `_greenhouse`; the page scrape is `_fetch_page`), `tests/test_fetch.py`.
Intent: A Greenhouse link (the embedded form `/embed/job_app?for=<board>&token=<id>`, or `/<board>/jobs/<id>`) is read from `boards-api.greenhouse.io`, which carries the full description; the page is scraped only when the API has nothing. Stripe's posting had been saved as its application form, so its documents were tailored without the job description.

## 2026-10-22 — Followed boards wait for a click; a skip list for companies
Asked: the 24 boards followed from Lenny's Jobs should list only ("I add"), and defense and space boards (SpaceX, SpaceX SI, Anduril, Varda, Stoke Space) removed and not followed again.
Changed: `scout/run.py` (`hold` on followed watches stops auto-queueing; `skipped` and the Lenny's watch's `args.skip` list), `tests/test_lennys.py`; data: the existing followed watches get `hold`, the five are removed, and the skip list is set on the person's Lenny's watch.
Intent: SpaceX and Anduril list ~2,500 roles each and a few hundred pass the title filter, many behind clearance or export control; auto-queueing them would have filled forms nobody chose. Roles from followed boards now wait in the Scout tab with "Add to autopilot". The skip list is the person's data, not names in the repository.

## 2026-10-22 — US only means US
Asked: "In different locations, check for only the United States."
Changed: `scout/filter.py` (`outside_us` keeps a place only when it reads as the US: the country, a state by name or by code after a comma, a major US city; an empty line, a bare "Remote"/"Hybrid", or "N locations" go on to the screen; named foreign places overrule a two-letter code), `tests/test_scout.py`.
Intent: The filter used to drop only places it knew to be abroad and kept the rest, so "Herzliya (Remote)", "Remoto" and any foreign city off its list came through; "Hyderabad, TS, IN" read as Indiana and "Toronto, CA" as California. Measured on the live Lenny's list: 39 of 50 kept, every kept row a US place, an empty line, or a bare remote.

## 2026-10-22 — A row with no location or company reads them off its link
Asked: "There are already jobs in Lenny's dashboard that are Bangalore-related. Can you remove all of them and redo Lenny?"
Changed: `scout/filter.py` (`place_in_url`: a Workday `/job/<place>/` stands in for an empty location), `scout/lennys.py` (`company_from_url` for a row with no company link), `tests/test_lennys.py`; data: the Bengaluru job it queued (a23d) deleted, the Lenny's watch reset and read again.
Intent: Accenture's rows on Lenny's carry no location text and no company link, so nine India roles read as "unknown place" and one was queued and tailored under the name "Lenny's Jobs" before it was stopped.

## 2026-10-22 — Clearance roles out everywhere; SpaceX kept
Asked: "Don't remove the ones like SpaceX ... we spoke about ... ones that require security clearance", then: keep SpaceX, skip Anduril, Varda and Stoke Space entirely.
Changed: `scout/filter.py` (`CLEARANCE_TITLE` drops a role whose title asks for a clearance; `_CLEARANCE` also reads "ability to obtain (and maintain) a ... clearance", TS/SCI and polygraph), `scout/run.py` (the Lenny's skip list applies to Lenny's own rows too), tests; data: the skip list is Anduril, Varda, Stoke Space, and SpaceX and SpaceX SI are followed again (held).
Intent: The line is the clearance, not the company: SpaceX has many roles without one. A skipped company is never followed, listed, screened or queued.

## 2026-10-22 — a16z and YC job boards in Scout
Asked: "Add a16z job board and Y Combinator job board as well." (Wellfound left out; see below.)
Changed: `scout/a16z.py`, `scout/yc.py` (new), `scout/providers.py`, `scout/__init__.py` (`detect`), `scout/run.py` (followed boards compared by their own arguments), `tests/test_boards.py`.
Intent: jobs.a16z.com renders ~25 newest cards per search, each linking to the employer's own form; a watch unites a few searches (software, ML, AI, data, new grad) and queues like Lenny's. ycombinator.com/jobs carries its postings as page data with minimum experience and a visa line; "4+ years" roles are left out in code, and the watch is list-only (`hold`) because applying is on Work at a Startup behind the person's YC login. Wellfound needs a login to search or apply and blocks automated access, so it is not a source. Measured live: a16z 122 roles, YC 27. The follow fix: comparing a board with its `via`/`hold` flags made every followed board look new and re-verified on every Lenny's check.

## 2026-10-22 — Leadership as an outreach option
Asked: "let's also have top partners, CEO, CFO, CTOs ... as well as an option, not just hiring managers and recruiters."
Changed: `outreach/apollo.py` (`leadership` titles: founders, CEO/CTO/CFO/COO, president, VP or head of engineering, partners; `discover(roles=)`), `outreach/models.py`, `outreach/store.py`, `server/outreach.py` (`DiscoverRequest.roles`), `review/index.html` (three tick boxes on Find people; Leadership in Add contact), `tailor/outreach_rules.md` (writing to leadership), `tests/test_outreach.py`.
Intent: Leadership is ticked by the person, never searched by default, and the automatic Lenny's drafts still go to a recruiter and a hiring manager. An email to a founder or executive leads with the company's problem and one proof point, is shorter, and only asks to be pointed to the right person: never a referral or their own time.

