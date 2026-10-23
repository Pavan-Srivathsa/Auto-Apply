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

## 2026-10-21 — One job brief behind every document (loop engineering, step 1)
Asked: "lets work on loop engineering and understand how to make the perfect resume for each. I should just review the key requirements from the job and highlights from my resume and then click apply." Jev for job identification and feedback.
Changed: `tailor/brief.py` and `tailor/brief_rules.md` (new), `tailor/jev.py` (new), `tailor/prompts.py` (`brief`, `brief.format`), `tailor/tailor.py` and `tailor/cover.py` (`brief=`), `pipeline.py` (`write_brief`), `.env.example` (`TYPESAFE_API_KEY`), `tests/test_brief.py`, `tests/test_pipeline.py`.
Intent: The posting is read once into up to eight ranked requirements, each with the line of the resume or a story that answers it; a quote that is not in the sources becomes a gap, and with a Jev key Jev turns a stretch into a gap too. The resume and the letter are written from that brief, saved as `brief.json`. A failed brief is a `brief_error.txt` and the documents are written as before. Next: the critic loop (step 2), the review card, Slack reply alerts and follow-ups.

## 2026-10-21 — The critic loop for the cover letter and cold emails (loop engineering, step 2)
Asked: "continue" (step 2 of the agreed plan); cold emails "should be well written"; Jev for feedback.
Changed: `tailor/critic.py` (new), `tailor/prompts.py` (`critic`, `critic.format`), `tailor/cover.py` (`revise`), `tailor/outreach.py` (`_refined`, `brief=`), `outreach/models.py` (`Draft.review`), `server/outreach.py` (passes the brief), `pipeline.py` (letter judged and revised, `cover_review.json`), `tests/test_critic.py`, `tests/test_pipeline.py`, `tests/test_lennys.py`.
Intent: Each letter and email is scored on a rubric of named points (email: opening, proof, ask, voice, subject; letter: requirements, why here, concrete, voice) plus an invention check, by Jev with a key and the text model without one. Points below "strong" and the job's top requirements left unanswered become fixed instructions; the writer revises at most twice, and a revision is kept only when it passes the writer's rules and scores higher. Measured on the Microsoft posting with GLM as judge: lenient (10/10 letter, 8/10 email with a generic opener) and noisy on invention, so it kept the original email; Jev is expected to judge tighter.

## 2026-10-21 — Jev judges for real
Asked: "i added it" (the Jev key, as `JEV_API_KEY`, `JEV_BASE_URL`, `JEV_MODEL`).
Changed: `tailor/jev.py` (reads `JEV_*` first, then `TYPESAFE_*`), `tailor/critic.py` (`sources`: the resume and stories, so the invention check sees everything the candidate may claim; the loop runs while any point needs a fix), `tailor/outreach.py`, `pipeline.py`, `.env.example`, tests.
Intent: Measured on the Microsoft posting with Jev: every brief quote judged a real match (0.74 to 0.96); the letter 9/10 as written; the email 8 to 9 with the opener rewritten (1.13 to 1.93). Before `sources`, Jev saw only the brief's quotes and capped true resume facts as invented.

## 2026-10-21 — What they want, what you have (loop engineering, step 3)
Asked: "I should just review the key requirements from the job and highlights from my resume and then click apply."
Changed: `server/review.py` (`fit` in the job detail, `_readable`), `review/index.html` (`fitHtml`, the card above the documents; the approve button reads Apply), `tests/test_brief.py`.
Intent: Each job opens on one card: every requirement the brief found, the resume or story line that answers it with Jev's confidence, whether the finished resume and letter answer it, gaps in red, and the letter's critic score. The button under it starts the same fill as before and still stops on the filled form for the person's Submit. Jobs tailored before the brief show no card.

## 2026-10-21 — Replies, Slack alerts and follow-ups (loop engineering, step 4)
Asked: Slack "should look exactly like" the person's screenshot (a positive-reply alert); follow-ups "yes": one draft after two days, approved by them.
Changed: `outreach/replies.py`, `outreach/slack.py`, `outreach/watch.py`, `outreach/drafting.py` (new), `tailor/replies.py` (new), `tailor/outreach.py` (`write_followup`), `tailor/prompts.py` (`outreach.followup`, `reply`), `outreach/models.py` (`Reply`, `Draft.followup`, `Draft.reply_to`, unique ids), `outreach/store.py` (`all_jobs`), `server/outreach.py` (follow-ups threaded on send; drafting helpers moved out), `server/app.py`, `pipeline.py`, `.env.example`, `tests/test_replies.py`, `tests/test_invariants.py`.
Intent: Every ten minutes the inbox is read read-only for answers to mail we sent; each is judged (Jev's verdict, a one-line why) and kept on the job, and a positive one is posted to Slack in the screenshot's shape with its Win #. A thread quiet for two days gets a follow-up drafted (two at most, none after any answer), sent only by the person, in the same thread. Nothing that runs on its own imports a mail sender; the invariant now covers the watcher and the pipeline, which is why drafting left `server/outreach.py`. Measured: IMAP login and read-only search work on the person's account; 9 sent emails, no answers yet, so 9 follow-ups will be drafted on the first check after a restart.

## 2026-10-21 — The resume in the loop too (loop engineering, step 5)
Asked: "Continue with the next steps."
Changed: `tailor/critic.py` (resume rubric: requirements, bullets, order; labelled `sources`; revisions compared on the exact rubric mean), `tailor/tailor.py` (`revision=True` skips the rewrite floor), `pipeline.py` (`review_resume`, `resume_review.json`), `server/review.py` and `review/index.html` (the resume's score on the card), tests.
Intent: After tailoring, Jev scores the resume against the brief; one revision starts from the tailored resume and goes through every tailoring check (lines, counts, links, invented figures, page count), kept only when its rubric mean beats the first by 0.03. Measured on the Microsoft posting: 1.27 to 1.33 (skills and bullet order toward the posting, roles kept in date order), 30 s; one run of three lost the revision to a GLM runaway (32k tokens) and kept the first resume, as designed. Found on the way: one concatenated blob of resume and stories made Jev call the untouched master "invented" (0.67); labelled parts read 0.22, so every judge now gets them labelled. The critic's order fix asked for "entries under each role" and the model moved a whole job; it now asks for bullets and keeps roles in date order. `rules.md` still allows reordering roles in the first pass; that is the person's call.

## 2026-10-21 — Jobs stay in date order; only projects move
Asked: "keep the jobs in date order. Only project around. Now keep it separate from the loop engineering part."
Changed: `tailor/tailor.py` (`roles`, `_check_role_order` in `_validate`), `tests/test_tailor.py`, and the person's own `tailor` prompt edit (two passages: entry order applies to PROJECTS only; jobs keep the master's order), saved through `tailor.prompts.save` so it merges with future stock updates.
Intent: A resume that moves a job above a newer one is sent back with the order named, like every other tailoring check; bullets inside a job and PROJECTS entries may still move. Committed on `mine`, apart from the `loop-engineering` branch.

## 2026-10-22 — Greenhouse postings read from the board API
Asked: testing the loop on the Stripe job ("Use stripe").
Changed: `tailor/fetch.py` (`greenhouse_job`, `_greenhouse`; the page scrape is `_fetch_page`), `tests/test_fetch.py`.
Intent: A Greenhouse link (the embedded form `/embed/job_app?for=<board>&token=<id>`, or `/<board>/jobs/<id>`) is read from `boards-api.greenhouse.io`, which carries the full description; the page is scraped only when the API has nothing. Stripe's posting had been saved as its application form, so its documents were tailored without the job description.

## 2026-10-22 — Am I qualified, and why not
Asked: "Is it possible for the feedback mechanism to tell us why we are not qualified for this particular job?" then "Build it"; a not-qualified job still tailors and waits instead of approving itself.
Changed: `tailor/qualify.py` (new), `tailor/prompts.py` (`qualify`), `pipeline.py` (`write_qualify`; `auto_approve` holds a not-qualified job; brief and verdict written through `archive.store`), `server/review.py` and `review/index.html` (the verdict at the top of the card, a level and reason on each row), `tests/test_qualify.py`.
Intent: Each requirement is rated meets / partly / missing against the resume and the stories (Jev's probabilities: missing at 30%, meets at 50%), with one plain line of reason; the verdict (Qualified / Stretch / Not qualified) is counted in code from the required items and the screen's hard flags. Measured: Vercel "Not qualified: none of the 6 required items fully met; 2 missing (TypeScript, Go)"; Stripe "Qualified: meets 5 of 7, partly 2". Rounding Jev's mean first had called every Vercel item "partly", TypeScript included.

## 2026-10-22 — An AI Engineering master resume, without the USC job
Asked: for AI engineering roles drop the USC research job and make Koantek and BlackBuckInsights stronger; keep USC for ML, fraud and GNN roles.
Changed: data only, nothing in the repository: `base/resumes/ai-engineering/` (from the Inference Resume: USC removed; BlackBuckInsights 5 bullets with the text-to-SQL pipeline and the FastAPI backend; Koantek 6 bullets leading with RAG, QLoRA fine-tuning, the Pearson speed-up and the Databricks workshop; skills rebalanced to AI and LLM engineering), and `data/resume_bases.json` (its target roles AI Engineer, Applied AI Engineer, LLM Engineer, GenAI Engineer, AI Solutions Engineer, AI Software Engineer; "AI Engineer" taken off the ML Resume). The registry before the change is backed up in the session scratchpad.
Intent: Every new bullet is a fact from the interview stories; one invented method ("by restructuring the computation on Databricks") was cut before it was saved. One page. Routing checked on real jobs: EchoStar, Apple and Centific AI Engineer roles pick it; Stripe ML and a fraud data-science role keep the ML Resume. Rules and checks are unchanged: each job is tailored against the master it was routed to.

## 2026-10-22 — AI Engineering master reverted
Asked: "dont edit the resume. ill work on that and send it to you."
Changed: data only: `data/resume_bases.json` restored from its backup, `base/resumes/ai-engineering/` removed (kept in the session scratchpad for reference). Routing is back to the three masters as they were, "AI Engineer" on the ML Resume.
Intent: The person writes their own master resumes; the new one is registered as they send it.

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

## 2026-10-22 — Am I qualified, in Scout, before anything is queued
Asked: "Can the model do this at the scout level itself ... so that I know exactly which I'm qualified for and I can directly put the qualified ones in?"
Changed: `scout/run.py` (`_fit`: routed master resume, picked stories, brief, qualification, the pipeline's own steps; `FIT_QUEUES`: only Qualified is auto-queued; `fit_listed` and `_keep_fit`), `scout/__init__.py` (`Hit.fit`), `server/scout.py` (`POST /scout/watch/{id}/fit`), `review/index.html` (`fitBlock` on Scout rows, Check fit on listed rows), `pipeline.py` (a Scout role keeps the board's title), tests; data: three jobs queued under "Lenny's Jobs" renamed to Affirm, Glean and Cross River, and "See Job Post" back to "Software Engineer II".
Intent: Every new role that passes the filters and the screen gets Qualified / Stretch / Not qualified with the requirements behind it, in the Scout tab; only Qualified ones go into autopilot by themselves, and any role can still be added by hand. Held boards' listed roles get a Check fit button. A fit that cannot be worked out never queues the role.

## 2026-10-22 — The bullet bank, and the ATS keyword count
Asked: draft experience bullets from the knowledge base, never change the master bullets, keep the good drafts in a separate store and reuse them "like a cache ... rather than regenerating again and again"; Scout's verdict unchanged.
Changed: `tailor/bank.py` (new: `jobs_on`, `story_for`, `ungrounded`, `judge`, `for_job`, `options`), `server/bank.py` (`/bullets` list and delete), `tailor/prompts.py` (`bank.draft`, `bank.format`), `tailor/tailor.py` (`options=`), `pipeline.py` (`write_bullets`, `bullets.json`; the banked bullets' stories join the tailoring's sources), `tailor/critic.py` (missing posting terms with evidence become resume fixes), `server/review.py` and `review/index.html` (ATS keywords on the card; the Bullet bank in the Resumes tab), `tests/test_bank.py`.
Intent: Per job on the resume, bullets are drafted for the posting from that job's story and its current bullets. A draft is banked only when every figure and name is on file (story or master, never the posting) and Jev rates it supported (0.7) and strong (1.4 of 2). Before drafting, banked bullets for that employer that use this posting's terms are pulled; drafting runs only when fewer than three are. The tailoring gets them as options under the same job; the master is never edited. Measured on Stripe: 8 banked in 8 s, one "3M+" rejected as not on file, four rated fair and left out. The bank lives in the person's data (`data/bullet_bank.json`).

## 2026-10-22 — The page loaded to "Loading…": an unclosed template
Asked: "Website is stuck."
Changed: `review/index.html` (the ATS block on the job card closed), `tests/test_invariants.py` (the page's script must parse, checked with `node --check`).
Intent: The ATS lines added to the card opened a `${... ? `<div>` and never closed it; the script failed to parse, so every freshly loaded tab stopped at "Loading…" while the server answered in milliseconds. Also stopped: an injector left running since 2026-09-28 from an old session, injecting the September banner into every tab beside the current one.

## 2026-10-22 — Leadership as an outreach option
Asked: "let's also have top partners, CEO, CFO, CTOs ... as well as an option, not just hiring managers and recruiters."
Changed: `outreach/apollo.py` (`leadership` titles: founders, CEO/CTO/CFO/COO, president, VP or head of engineering, partners; `discover(roles=)`), `outreach/models.py`, `outreach/store.py`, `server/outreach.py` (`DiscoverRequest.roles`), `review/index.html` (three tick boxes on Find people; Leadership in Add contact), `tailor/outreach_rules.md` (writing to leadership), `tests/test_outreach.py`.
Intent: Leadership is ticked by the person, never searched by default, and the automatic Lenny's drafts still go to a recruiter and a hiring manager. An email to a founder or executive leads with the company's problem and one proof point, is shorter, and only asks to be pointed to the right person: never a referral or their own time.

## 2026-10-22 — Every listed role gets its fit by itself
Asked: "Why is 'check fit' an option? Let it automatically check the fit. I am not clicking another thing."
Changed: `scout/fitter.py` (new: a server thread that works out the fit of every listed role without one, two at a time, newest first, cached by link in `data/fit_cache.json`; an unreadable posting is `unknown`, not retried), `scout/store.py` (`set_listed_fit`, `fits_by_id`, under the lock), `scout/run.py` (a rewritten list keeps the fits in the store, not the ones the check started with), `server/app.py`, `review/index.html` (the button gone; "Checking fit…" until the verdict lands), `tests/test_fitter.py`.
Intent: The verdict is on every Scout row before the person looks. The first pass covers the ~240 roles already listed; after that each check adds a few.

## 2026-10-22 — A failed model call is not "unknown"; the free steps decide first
Asked: (found while turning on automatic fits) the OpenRouter key hit its monthly limit ("$5.05 of $5.00 used; resets 2026-11-01"), and the checker recorded 31 of its first 48 fits as "unknown" and cached them.
Changed: `scout/run.py` (`fit_or_raise`: an unfetchable page is `unknown`; a pre-screen hit is Not qualified with no model call; a failed model or Jev step raises `FitUnavailable` and `_fit` returns {}), `scout/fitter.py` (nothing written or cached for {}; a pass that writes nothing while rows wait backs off 15 minutes), `tests/test_fitter.py`; data: the 31 wrong "unknown" fits cleared from the cache and the lists.
Intent: A refused API call says nothing about the role, so it is tried again later; and with ~440 listed roles on a $5 key, a role the code can rule out costs nothing.

## 2026-10-22 — A refused model call says why, not "HTTP 500"
Asked: Write emails answered "HTTP 500".
Changed: `server/app.py` (an `LLMError` anywhere becomes a 502 with the reason: the key's spending limit, a refused key, or the error's first line), `tests/test_outreach.py`.
Intent: The OpenRouter key had hit its monthly limit and the error escaped as a crash. The person has since put a new key in the data folder's `.env` (limit $50); the server is restarted to read it.

## 2026-10-22 — Cold emails that pass the loop, and differ person to person
Asked: "The email generator is really bad. Put it into a loop, and make sure it works again ... test it on Alchemi."
Changed: `tailor/outreach.py` (code checks: the subject opens with the reader's first name and a hook, "Hi <first>," greeting, and no email may repeat a colleague's wording, opening sentence or figures; `write(peers=)`; the posting is a source), `outreach/drafting.py` (`peers`: the emails already written to the job's other contacts), `tailor/critic.py` (the posting may back statements about the company; a flagged email is told which sentence to cut), `tailor/outreach_rules.md` (no bridging claims; the subject and greeting), tests.
Intent: On Alchemi the loop scored two of three drafts 3/10 for "invented" sentences ("a bar I have worked against"), told the writer only "remove every unsupported claim", and threw every revision away; all three were near-copies with the subject "AI Engineer role at Alchemi". Measured after: 9, 8 and 8, three openings and three proof points (BlackBuckInsights and DAPTA; Koantek RAG and fine-tuning; USC fraud evaluation), subjects like "Sam, agents for drug development timelines".

## 2026-10-22 — The 3/10 cap on letters, and Go "on file"
Asked: "what 3/10, for resume and cover letter? but match is 7/7?" (Glean, ML Engineer, Assistant Quality).
Changed: `pipeline.py` (the letter's invention check gets the posting as a source), `server/review.py` (`_ats`: a keyword is "on file" only when that term is in the master resume or the stories), `tailor/critic.py` (the resume's keyword fixes only for such terms), `tests/test_bank.py`.
Intent: 3/10 was the invention cap, not quality: the letter's points were 1.99, 1.97, 2.0 and 1.5 (about 9/10) but its true lines about Glean had no source to rest on. The ATS list put Go and Java "on file" because Python answered the languages requirement, and the resume's revision was told to use them.


## 2026-10-22 — Judge invention line by line, and let the code prove a line

Intent: Glean's resume read 3/10 because one whole-document question had Jev
call it 0.70 "invented" while every bullet alone scored 0.28 or less. Jev now
judges each sentence or bullet, and a line whose figures and names are all on
the master or the picked stories is proven by the code (`bank.ungrounded`), so
Jev's doubt alone cannot cap it. A line with nothing checkable stays Jev's call.
The resume critic reads the visible bullets, not the LaTeX. Glean re-scored 7/10.

## 2026-10-22 — No visa sponsorship is checked first, and the Scout never applies

Intent: "Check this first always. Don't even apply through Scout if this is true
for the job." `filter.prescreen` now tests for a refusal to sponsor (YC's "US
citizen/visa only", "unable to sponsor", "without current or future
sponsorship", "US citizens or permanent residents only", "not open to OPT/CPT")
before years, clearance or citizenship, so the Scout's screen is a reject with
no model call. YC rows with that visa line are never listed, and `queue_hit`
refuses such a hit, including the hand "Add to autopilot" (which now reads the
description first).

## 2026-10-22 — A form's sponsorship question is not a refusal

Intent: from the original repo (85487d2), ported. "Are you authorized to work
in the U.S. without sponsorship?" and the answer "Yes, I am authorized ...
without sponsorship" read as the employer refusing, so a posting captured on
its form would be rejected. A match in a question, or in a sentence that opens
like an answer ("Yes,", "No,", "I am"), no longer counts.

## 2026-10-22 — Read the posting from the board's own API

Intent: from the original repo (e2c600b), ported. Most captures are the
application form, not the description. `tailor/boards.py` (taken as is) asks
Ashby, Greenhouse, Lever and Workday's public endpoints first; that copy wins
outright in `pipeline.fetch_posting` (`Posting.authoritative`), and a page that
reads as a form (`quality.looks_like_form`) scores zero against the others.
