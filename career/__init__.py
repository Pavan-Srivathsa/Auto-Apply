"""Career intelligence: what happened to each application, and what that says.

- `events`: the outcome ledger. Every event is either the person's
  (`source: "user"`) or inferred from evidence the app already holds
  (`source: "inferred"`, with `how`). Nothing here changes a job's status,
  and no rejection is ever inferred from silence.
- `snapshot`: what the app believed about a job at the moment it was
  submitted (fit, policy version, resume version, the posting, the skills
  matched and missing), frozen so later scoring changes cannot rewrite it.
- `analytics`: the funnel, calibration of fit against outcomes, bottlenecks
  and recommendations, always with denominators and 95% intervals, and no
  rate at all below a sample floor.

Design ideas (in-flight applications are not data points; no rate below a
sample floor; evidence precedence; strictly advisory) follow Career-Ops'
calibrate.mjs and funnel-stages.mjs (github.com/career-ops-hq/career-ops,
MIT), reimplemented here; no code was copied.
"""
