"""The ML/DS technical review of a tailored resume, bullet by bullet.

The critic (tailor/critic.py) asks whether the resume as a whole reads well
for the job. This asks the narrower question that comes first: is each
rewritten bullet still true to what it was rewritten from, technically and
factually. Writing quality never decides it.

Per rewritten bullet:

1. **Provenance.** Its source is the master bullet it came from (paired by
   text similarity, so reordering is fine), or, when it reads more like a
   story than any master bullet, the story lines it came from. Only that
   source counts as support: a figure that is on the master in another
   bullet does not support this one.
2. **Claims in code.** Figures, names (tools, models, datasets, employers),
   scope verbs ("Led", "Owned") and durations, each verified against the
   source or not. An unverified figure is a FAIL by itself.
3. **Jev**, batched: nine narrow typed questions per bullet (support,
   metrics, ownership, causality, ML terminology, technologies, detail
   kept, relevance, readability). Jev never writes anything here.
4. **Verdict** from the code claims and Jev's probabilities against
   calibrated thresholds (`reviewer_thresholds.json`, measured on the
   labeled benchmark in `bench/`): PASS, WARN, FAIL, or UNKNOWN when no judge
   could answer. Unknown is never a pass.
5. **Escalation.** A WARN (Jev unsure, or a claim the code cannot verify)
   goes, alone, to a reasoning model that answers with a verdict, an issue
   type and a quote from the source; it may not propose new facts.

Decisions are cached by rubric version, mode, source, bullet and the
posting's requirements, so an unchanged bullet is not reviewed twice.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import paths

from . import costs, jev, llm, prompts
from . import layout
from . import tailor as tailoring

RUBRIC_VERSION = "mlds-2026-10-22.2"
PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
VERDICTS = (PASS, WARN, FAIL, UNKNOWN)

# The material criteria: a "no" on any of these is a claim the source does not
# carry, and blocks approval. `detail` warns; relevance and readability only
# describe quality and never decide a verdict.
MATERIAL = ("support", "metrics", "ownership", "causal", "technical", "technology")
QUESTIONS = {
    "support": ("Is every claim in `pairs[{i}].rewritten` supported by `pairs[{i}].source`?",
                "Rewording is fine. A result, scale, tool, user group or scope the source does not "
                "state is not supported, even if it sounds plausible."),
    "metrics": ("Does every number in `pairs[{i}].rewritten` keep the source's value, unit and meaning?",
                "Same value, same unit, same thing measured, attached to the same model or system. "
                "Latency is not throughput; percent is not percentage points; a benchmark score is not a "
                "production result; a metric of one model is not another's. No numbers at all counts as yes."),
    "ownership": ("Does `pairs[{i}].rewritten` claim no more ownership, leadership or scope than `pairs[{i}].source`?",
                  "Built, contributed to, led, owned, architected and managed are different claims. A team "
                  "effort is not a solo one; part of a system is not the whole system."),
    "causal": ("Does `pairs[{i}].rewritten` claim that the work caused a result which `pairs[{i}].source` does not say the work caused?",
               "Analysed an observed rise is not drove the rise; contributed to a saving is not achieved it. "
               "A result the source already credits to the work, reworded, is not a new causal claim."),
    "technical": ("Is the ML and data-science terminology in `pairs[{i}].rewritten` correct and true to `pairs[{i}].source`?",
                  "Same stage: training, fine-tuning, inference, evaluation and deployment are different "
                  "work. Same method: RAG, embeddings, retrieval, benchmarking and optimisation are not "
                  "interchangeable. A term used wrongly is a no."),
    "technology": ("Does `pairs[{i}].rewritten` name only technologies, models and datasets that `pairs[{i}].source` names, or exact equivalents?",
                   "A different library, model, cloud or database is a substitution, not a paraphrase."),
    "detail": ("Does `pairs[{i}].rewritten` keep the important technical detail of `pairs[{i}].source`?",
               "Dropping the model, the dataset size, the method or the measured result loses detail."),
}
SCORES = {
    "relevance": ("How relevant is `pairs[{i}].rewritten` to `requirements`?",
                  ["Not relevant to anything the posting asks", "Related to one requirement",
                   "Directly answers a top requirement in the posting's terms"]),
    "readability": ("How readable is `pairs[{i}].rewritten` as a resume bullet?",
                    ["Hard to follow or padded", "Clear but loose", "One clear idea, result first, tight"]),
}
ISSUES = {
    "support": "unsupported claim", "metrics": "metric changed or misattributed",
    "ownership": "inflated ownership", "causal": "causal claim the source does not make",
    "technical": "ML terminology or stage wrong", "technology": "technology substituted or added",
    "detail": "technical detail lost",
}
INVERTED = {"causal"}   # questions where "yes" is the problem; stored as P(accurate) like the rest
ISSUE_TYPES = tuple(ISSUES.values()) + ("unverified figure", "fabricated impact", "none")

THRESHOLDS = Path(__file__).resolve().parent / "reviewer_thresholds.json"
# pass_at: every material P(accurate) at or above it passes. fail_at: one below it
# fails. escalate_at: a Jev-only FAIL whose P is at or above it is sent to the
# reasoning model instead of standing (Jev alone swung one bullet from 0.97 to
# 0.20 between two runs). Calibrated on bench/ by tools/bench_reviewer.py.
DEFAULT_THRESHOLDS = {"pass_at": 0.8, "fail_at": 0.35, "escalate_at": 0.1}
BATCH = 6              # bullets per Jev request (nine questions each)
STORY_MARGIN = 0.1     # how much better a story window must overlap than the paired master bullet
SAME = 0.97            # a rewrite this close to its source is the source


def thresholds() -> dict:
    """Calibrated thresholds, then the environment's overrides."""
    found = dict(DEFAULT_THRESHOLDS)
    try:
        found.update({k: float(v) for k, v in json.loads(THRESHOLDS.read_text()).get("thresholds", {}).items()})
    except (OSError, ValueError):
        pass
    for key, env in (("pass_at", "AUTOPILOT_REVIEW_PASS_AT"), ("fail_at", "AUTOPILOT_REVIEW_FAIL_AT"),
                     ("escalate_at", "AUTOPILOT_REVIEW_ESCALATE_AT")):
        if os.environ.get(env):
            found[key] = float(os.environ[env])
    return found


def review_model() -> str:
    """The reasoning model a bullet is escalated to."""
    return os.environ.get("OPENROUTER_REVIEW_MODEL") or llm.tailor_model()


# ------------------------------------------------------------ provenance --
@dataclass
class Pair:
    index: int                    # position in the tailored resume, from 0
    rewritten: str                # visible text
    original: str                 # the master bullet it was paired with ("" when none)
    source: str                   # what supports it: the original, or story lines
    source_kind: str              # "master" | "story:<slug>" | "none"
    original_index: Optional[int] = None
    rewritten_tex: str = ""
    original_tex: str = ""


def _visible(tex_bullet: str) -> str:
    return " ".join(layout.visible(tex_bullet).split())


def _overlap(a: str, b: str) -> float:
    wa, wb = tailoring._words(a), tailoring._words(b)
    return len(wa & wb) / len(wa) if wa else 0.0


def _story_lines(bullet: str, story: str, limit: int = 8) -> str:
    """The lines of one story that carry this bullet's terms, figures or
    names, in the story's order. A story keeps its facts on separate lines
    (`Stack:`, `Numbers:`), so a contiguous window misses them; the whole
    story would be the "anywhere on file" this review refuses to accept."""
    words = tailoring._words(bullet)
    figures = tailoring._numbers(bullet)
    names = {stem for stem in tailoring._named(bullet)}
    scored = []
    for n, line in enumerate(l.strip() for l in story.splitlines()):
        if len(line) < 8:
            continue
        lw = tailoring._words(line)
        score = len(words & lw) + 3 * len(figures & tailoring._numbers(line)) + 2 * len(names & lw)
        if score >= 3:
            scored.append((score, n, line))
    keep = sorted(sorted(scored, reverse=True)[:limit], key=lambda row: row[1])
    return "\n".join(line for _, _, line in keep)


def _employers(tex: str) -> dict[str, str]:
    """Visible bullet text -> the employer it sits under (EXPERIENCE only)."""
    from . import bank
    try:
        return {b: job["company"] for job in bank.jobs_on(tex) for b in job["bullets"]}
    except Exception:  # noqa: BLE001 - no roles read means no employer evidence, not an error
        return {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


SWAP = 0.45            # below this similarity to its master bullet, a bullet may be a story swap


def pairs(master_tex: str, tailored_tex: str, stories: Optional[dict[str, str]] = None) -> list[Pair]:
    """Each tailored bullet with its source. Greedy unique matching by
    similarity, so a reordered bullet keeps its own source. The source is the
    paired master bullet plus the lines of its own employer's story that
    carry its terms; a bullet unlike its master bullet that a story explains
    better is a story swap, judged against that story's lines alone."""
    import difflib

    stories = stories or {}
    before_tex, after_tex = tailoring.bullets(master_tex), tailoring.bullets(tailored_tex)
    before = [_visible(b) for b in before_tex]
    after = [_visible(b) for b in after_tex]
    ratio = {(i, j): difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
             for i, a in enumerate(before) for j, b in enumerate(after)}
    match: dict[int, int] = {}
    used: set[int] = set()
    for (i, j), _ in sorted(ratio.items(), key=lambda kv: kv[1], reverse=True):
        if j not in match and i not in used:
            match[j] = i
            used.add(i)
    employer_of = _employers(tailored_tex)
    out = []
    for j, text in enumerate(after):
        i = match.get(j)
        original = before[i] if i is not None else ""
        source, kind = original, "master" if original else "none"
        company = _slug(employer_of.get(text, ""))[:12]
        own = next((slug for slug in stories if company and company in _slug(slug)), "")
        if own:
            lines = _story_lines(text, stories[own])
            if lines:
                source, kind = (original + "\n" + lines).strip(), (f"master+story:{own}" if original else f"story:{own}")
        if stories and (i is None or ratio[(i, j)] < SWAP):
            best = max(((slug, _story_lines(text, body)) for slug, body in stories.items()),
                       key=lambda sl: _overlap(text, sl[1]))
            if best[1] and _overlap(text, best[1]) > _overlap(text, original) + STORY_MARGIN:
                source, kind = best[1], f"story:{best[0]}"
        out.append(Pair(j, text, original, source, kind, i, after_tex[j],
                        before_tex[i] if i is not None else ""))
    return out


# ---------------------------------------------------------------- claims --
# Two inflations Jev scored as accurate on a real resume (Glean, 2026-10-22):
# "contributing to $3M in savings" -> "achieving ... $3M", and "Benchmarked"
# -> "Designed an evaluation framework" (causal 0.71, ownership 0.82). Neither
# adds a figure or a name. So the code notices a hedge the source had and the
# rewrite dropped, and a lead verb stronger than the source's; neither fails a
# bullet, both send it to the reasoning model (WARN).
HEDGE = re.compile(r"\b(contribut\w*\s+to|help(?:ed|ing)?|assist(?:ed|ing)?|support(?:ed|ing)|"
                   r"participat\w*|part of|collaborat\w*|as part of|team of \w+|co-\w+|"
                   r"prototyp\w*|propos\w*|explor\w*|observed|analy[sz]\w*)\b", re.I)
STRONG = {"led", "lead", "designed", "architected", "owned", "drove", "spearheaded", "launched",
          "pioneered", "established", "founded", "directed", "headed", "managed", "shipped",
          "delivered", "created", "invented", "transformed", "increased", "grew", "boosted", "drove"}


def _lead(text: str) -> str:
    match = re.match(r"\W*([A-Za-z-]+)", text)
    return match.group(1).lower() if match else ""


def inflations(rewritten: str, source: str) -> list[str]:
    """Hedges the source states and the rewrite drops, and a lead verb the
    source does not support. Not proof of an error; a reason to look."""
    low = rewritten.lower()
    found = [f"drops '{m.group(0).lower()}'" for m in HEDGE.finditer(source)
             if tailoring._stem(m.group(0).split()[0]) not in tailoring._words(low)]
    verb, was = _lead(rewritten), _lead(source)
    if verb in STRONG and verb != was and tailoring._stem(verb) not in tailoring._words(source):
        found.append(f"lead verb '{was}' became '{verb}'")
    return list(dict.fromkeys(found))


def claims(rewritten: str, source: str) -> dict:
    """The checkable claims of `rewritten`, verified against `source` alone."""
    figures = sorted(tailoring._numbers(rewritten))
    have = tailoring._numbers(source)
    known = tailoring._words(source)
    names = sorted({word for stem, word in tailoring._named(rewritten).items()})
    named_ok = {word for stem, word in tailoring._named(rewritten).items() if tailoring._is_known(stem, known)}
    scope = tailoring.unsupported_claims(rewritten, source)
    verified = [f for f in figures if f in have] + [n for n in names if n in named_ok]
    unverified = ([f"figure {f}" for f in figures if f not in have]
                  + [f"name {n}" for n in names if n not in named_ok]
                  + [f"scope {c}" for c in scope]
                  + [f"inflation: {i}" for i in inflations(rewritten, source)])
    return {"verified": verified, "unverified": unverified,
            "unverified_figures": [f for f in figures if f not in have]}


# --------------------------------------------------------------- results --
@dataclass
class BulletReview:
    index: int
    original: str
    rewritten: str
    source: str
    source_kind: str
    verdict: str = UNKNOWN
    issue_type: str = "none"
    issue: str = ""
    evidence: str = ""
    confidence: float = 0.0
    scores: dict = field(default_factory=dict)        # criterion -> P(yes) or 0..2 score
    verified_claims: list = field(default_factory=list)
    unverified_claims: list = field(default_factory=list)
    method: str = ""                                   # unchanged | code | jev | jev+llm | llm | none
    escalated: bool = False
    correction: str = ""                               # the writer's revision, when one was asked for
    correction_verdict: str = ""
    decision: str = ""                                 # the person's accept / reject / edit
    edited: str = ""                                   # the person's own wording, when they edited it
    rewritten_tex: str = ""                            # the bullet as it is in resume.tex
    original_tex: str = ""                             # the master's bullet, for putting it back

    @property
    def blocking(self) -> bool:
        return self.verdict in (FAIL, UNKNOWN, WARN) and self.decision not in ("accept", "edit")


@dataclass
class Review:
    rubric: str = RUBRIC_VERSION
    mode: str = "hybrid"
    bullets: list[BulletReview] = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""
    rounds: list = field(default_factory=list)          # the controlled revision's trace
    reverted: list = field(default_factory=list)        # indexes put back to the master's wording

    @property
    def counts(self) -> dict:
        return {v: sum(1 for b in self.bullets if b.verdict == v) for v in VERDICTS}

    @property
    def needs_human(self) -> bool:
        """True when anything is unresolved: a FAIL, WARN or UNKNOWN the person
        has not accepted, a bullet put back to its master wording, or no review."""
        return bool(self.error) or bool(self.reverted) or any(b.blocking for b in self.bullets)

    def to_dict(self) -> dict:
        return {"rubric": self.rubric, "mode": self.mode, "counts": self.counts,
                "needs_human": self.needs_human, "thresholds": self.thresholds, "cost": self.cost,
                "seconds": round(self.seconds, 2), "error": self.error, "rounds": self.rounds,
                "reverted": self.reverted, "bullets": [asdict(b) for b in self.bullets]}

    @classmethod
    def from_dict(cls, data: dict) -> "Review":
        known = set(BulletReview.__dataclass_fields__)
        bullets = [BulletReview(**{k: v for k, v in b.items() if k in known}) for b in data.get("bullets", [])]
        return cls(rubric=data.get("rubric", ""), mode=data.get("mode", ""), bullets=bullets,
                   thresholds=data.get("thresholds", {}), cost=data.get("cost", {}),
                   seconds=data.get("seconds", 0.0), error=data.get("error", ""),
                   rounds=data.get("rounds", []), reverted=data.get("reverted", []))


# ----------------------------------------------------------------- cache --
_cache_lock = threading.Lock()


def _cache_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_REVIEW_CACHE") or paths.DATA / "review_cache.json")


def _key(mode: str, pair: Pair, requirements: list[str]) -> str:
    blob = json.dumps([RUBRIC_VERSION, mode, thresholds(), pair.source, pair.rewritten, requirements])
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_get(key: str) -> Optional[dict]:
    try:
        return json.loads(_cache_path().read_text()).get(key)
    except (OSError, ValueError):
        return None


def _cache_put(entries: dict[str, dict]) -> None:
    if not entries:
        return
    path = _cache_path()
    with _cache_lock:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        data.update(entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)


# ------------------------------------------------------------------- Jev --
def _jev_scores(batch: list[Pair], requirements: list[str]) -> list[dict]:
    """P(yes) for every material criterion and `detail`, and 0..2 for
    relevance and readability, for each pair in one request."""
    state = {"pairs": [{"source": p.source, "rewritten": p.rewritten} for p in batch],
             "requirements": requirements[:12]}
    questions = {}
    for i in range(len(batch)):
        for name, (question, focus) in QUESTIONS.items():
            questions[f"{name}_{i}"] = jev.noul({"question": question.format(i=i), "focus": focus})
        for name, (question, levels) in SCORES.items():
            questions[f"{name}_{i}"] = jev.score(question.format(i=i), levels)
    answers = jev.ask(state, questions)
    out = []
    for i in range(len(batch)):
        row = {name: round(1 - float(answers[f"{name}_{i}"]["noul"]) if name in INVERTED
                           else float(answers[f"{name}_{i}"]["noul"]), 3) for name in QUESTIONS}
        row |= {name: round(float(answers[f"{name}_{i}"]["score"]), 3) for name in SCORES}
        out.append(row)
    return out


# --------------------------------------------------------- reasoning LLM --
PROMPT = """You check one rewritten resume bullet against the only text that may support it.
You are an ML and data-science reviewer. Factual correctness first, technical accuracy second.

FAIL when the rewrite states anything the source does not: a result, number, unit, tool, model,
dataset, scope, user group or impact; when it changes what a metric measures or which model it
belongs to (latency vs throughput, percent vs points, benchmark vs production); when it confuses
training, fine-tuning, inference, evaluation and deployment, or benchmarking with optimisation;
when it turns an observation or correlation into a cause; or when it inflates ownership (built ->
led, part -> whole, team -> solo).
PASS when every claim is the source's, reworded. Adding the posting's name for the same work is
fine only when it is an exact equivalent. Dropping detail alone is not a FAIL.
UNCERTAIN when the source does not say enough to decide.

Never suggest replacement wording and never supply a fact.
"""
FORMAT = f"""Reply with one fenced block and nothing else:

```json
{{"verdict": "PASS" | "FAIL" | "UNCERTAIN",
 "issue_type": one of {json.dumps(list(ISSUE_TYPES))},
 "issue": "<one sentence naming the exact problem, or empty>",
 "evidence": "<a short exact quote from the source that shows it, or empty>"}}
```
"""


def _llm_check(pair: Pair, requirements: list[str], model: Optional[str] = None) -> dict:
    user = (f"## Source\n\n{pair.source or '(no source: nothing on file supports this bullet)'}\n\n"
            f"## Rewritten\n\n{pair.rewritten}\n\n## The posting asks for\n\n"
            + "\n".join(f"- {r}" for r in requirements[:12]))
    reply = llm.complete(prompts.text("reviewer") + "\n" + prompts.text("reviewer.format"), user,
                         model=model or review_model(), temperature=0.0, max_tokens=900)
    match = re.search(r"```json[^\n]*\n(.*?)```", reply, re.S) or re.search(r"(\{.*\})", reply, re.S)
    if not match:
        raise llm.LLMError(f"reviewer reply had no JSON: {reply[:120]!r}")
    data = json.loads(match.group(1))
    verdict = str(data.get("verdict", "")).upper()
    if verdict not in ("PASS", "FAIL", "UNCERTAIN"):
        raise llm.LLMError(f"reviewer verdict {verdict!r} is not PASS, FAIL or UNCERTAIN")
    issue_type = data.get("issue_type") if data.get("issue_type") in ISSUE_TYPES else "unsupported claim"
    evidence = str(data.get("evidence") or "")
    # A quote that is not in the source is not evidence.
    if evidence and " ".join(evidence.lower().split()) not in " ".join(pair.source.lower().split()):
        evidence = ""
    return {"verdict": verdict, "issue_type": issue_type if verdict != "PASS" else "none",
            "issue": str(data.get("issue") or "")[:300], "evidence": evidence[:300]}


# --------------------------------------------------------------- verdicts --
def _decide(pair: Pair, scores: Optional[dict], found: dict, limits: dict) -> BulletReview:
    review = BulletReview(pair.index, pair.original, pair.rewritten, pair.source, pair.source_kind,
                          verified_claims=found["verified"], unverified_claims=found["unverified"],
                          rewritten_tex=pair.rewritten_tex, original_tex=pair.original_tex)
    if found["unverified_figures"]:
        review.verdict, review.issue_type = FAIL, "unverified figure"
        review.issue = (f"states {', '.join(found['unverified_figures'])}, which its source "
                        f"({pair.source_kind}) does not")
        review.confidence, review.method = 1.0, "code"
        review.evidence = pair.source
    if scores is None:
        return review
    review.scores = scores
    lowest = min(MATERIAL, key=lambda name: scores[name])
    p = scores[lowest]
    if review.verdict == FAIL:
        review.method = "code+jev"
        return review
    review.method = "jev"
    if p < limits["fail_at"]:
        review.verdict, review.issue_type = FAIL, ISSUES[lowest]
        review.issue = f"Jev: {ISSUES[lowest]} (P(accurate) {p:.2f})"
        review.confidence, review.evidence = round(1 - p, 3), pair.source
    elif p >= limits["pass_at"] and not found["unverified"]:
        review.verdict, review.confidence = PASS, round(p, 3)
        if scores["detail"] < limits["fail_at"]:
            review.verdict, review.issue_type = WARN, ISSUES["detail"]
            review.issue = f"Jev: {ISSUES['detail']} (P(kept) {scores['detail']:.2f})"
    else:
        review.verdict, review.confidence = WARN, round(p, 3)
        review.issue_type = ISSUES[lowest] if p < limits["pass_at"] else "unsupported claim"
        review.issue = (f"Jev unsure: {ISSUES[lowest]} (P(accurate) {p:.2f})" if p < limits["pass_at"]
                        else "cannot verify in code: " + ", ".join(found["unverified"]))
        review.evidence = pair.source
    return review


def _apply_llm(review: BulletReview, answer: dict, method: str) -> None:
    review.escalated = True
    review.method = method
    review.verdict = {"PASS": PASS, "FAIL": FAIL, "UNCERTAIN": WARN}[answer["verdict"]]
    review.issue_type = answer["issue_type"]
    review.issue = answer["issue"] or review.issue
    review.evidence = answer["evidence"] or review.evidence
    if review.verdict == PASS:
        review.issue = ""


def judge_pairs(found_pairs: list[Pair], requirements: Optional[list[str]] = None, mode: str = "hybrid",
                escalate: Optional[bool] = None, use_cache: bool = True) -> list[BulletReview]:
    """A verdict for every pair, in order. The unit the benchmark measures:
    `review` reads the pairs off two resumes and calls this. Never raises."""
    requirements = requirements or []
    limits = thresholds()
    if escalate is None:
        escalate = os.environ.get("AUTOPILOT_REVIEW_ESCALATE", "1") != "0"
    reviews: dict[int, BulletReview] = {}
    todo: list[Pair] = []
    for pair in found_pairs:
        found = claims(pair.rewritten, pair.source)
        if pair.original and tailoring._normalise(pair.rewritten) == tailoring._normalise(pair.original):
            reviews[pair.index] = BulletReview(pair.index, pair.original, pair.rewritten, pair.source,
                                               pair.source_kind, verdict=PASS, confidence=1.0,
                                               method="unchanged", verified_claims=found["verified"],
                                               rewritten_tex=pair.rewritten_tex, original_tex=pair.original_tex)
            continue
        hit = _cache_get(_key(mode, pair, requirements)) if use_cache else None
        if hit:
            # The decision is the cached one; where the bullet sits is today's.
            r = BulletReview(**{k: v for k, v in hit.items() if k in BulletReview.__dataclass_fields__})
            r.index, r.original, r.rewritten_tex, r.original_tex = (pair.index, pair.original,
                                                                     pair.rewritten_tex, pair.original_tex)
            r.method = r.method if r.method.endswith("(cached)") else f"{r.method} (cached)"
            reviews[pair.index] = r
            continue
        todo.append(pair)

    def judge_batch(batch: list[Pair]) -> list[BulletReview]:
        out = []
        if mode == "llm":
            for pair in batch:
                found = claims(pair.rewritten, pair.source)
                r = _decide(pair, None, found, limits)
                try:
                    _apply_llm(r, _llm_check(pair, requirements), "llm")
                    if found["unverified_figures"]:
                        r.verdict = FAIL            # a code-proven invented figure stays a FAIL
                except Exception as exc:  # noqa: BLE001
                    r.verdict, r.method, r.issue = UNKNOWN, "none", f"reviewer unavailable: {str(exc)[:120]}"
                out.append(r)
            return out
        try:
            scores = _jev_scores(batch, requirements) if jev.configured() else None
        except Exception as exc:  # noqa: BLE001 - UNKNOWN, never a silent pass
            scores, why = None, f"Jev unavailable: {str(exc)[:120]}"
        else:
            why = "" if scores is not None else "Jev is not configured"
        for k, pair in enumerate(batch):
            r = _decide(pair, scores[k] if scores else None, claims(pair.rewritten, pair.source), limits)
            if scores is None and r.verdict != FAIL:
                r.verdict, r.method, r.issue = UNKNOWN, "none", why
            jev_only_fail = (r.verdict == FAIL and r.method == "jev"
                             and min(r.scores[m] for m in MATERIAL) >= limits["escalate_at"])
            if mode == "hybrid" and escalate and (r.verdict in (WARN, UNKNOWN) or jev_only_fail) \
                    and llm_configured():
                try:
                    _apply_llm(r, _llm_check(pair, requirements), "jev+llm" if scores else "llm")
                except Exception as exc:  # noqa: BLE001 - stays as it was, for the person
                    r.issue = (r.issue + f"; escalation failed: {str(exc)[:80]}").strip("; ")
            out.append(r)
        return out

    open_ledgers = list(costs._open())
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    runner = _with_ledger(judge_batch, open_ledgers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(runner, batches))
    fresh: dict[str, dict] = {}
    for batch, judged in zip(batches, done):
        for pair, r in zip(batch, judged):
            reviews[pair.index] = r
            if r.verdict != UNKNOWN and use_cache:
                fresh[_key(mode, pair, requirements)] = asdict(r)
    _cache_put(fresh)
    return [reviews[i] for i in sorted(reviews)]


def review(master_tex: str, tailored_tex: str, requirements: Optional[list[str]] = None,
           stories: Optional[dict[str, str]] = None, mode: str = "hybrid",
           escalate: Optional[bool] = None) -> Review:
    """Review every bullet of `tailored_tex` against its own source. `mode` is
    "hybrid" (Jev; the reasoning model for WARN, UNKNOWN and a Jev-only FAIL
    above `escalate_at`), "jev" (no escalation) or "llm" (the reasoning model
    on every changed bullet). Never raises: a judge that fails leaves UNKNOWN,
    and UNKNOWN is never a pass."""
    started = time.monotonic()
    result = Review(mode=mode, thresholds=thresholds())
    with costs.track() as ledger:
        try:
            found_pairs = pairs(master_tex, tailored_tex, stories)
        except Exception as exc:  # noqa: BLE001 - an unreadable resume is UNKNOWN, not a pass
            result.error = f"could not read the bullets: {exc}"
            return result
        result.bullets = judge_pairs(found_pairs, requirements, mode, escalate)
    result.cost = ledger.to_dict()
    result.seconds = time.monotonic() - started
    return result


def _with_ledger(fn, ledgers: list):
    """Run `fn` in a worker thread with the caller's open ledgers open there too."""
    def run(*args):
        mine = costs._open()
        mine.extend(ledgers)
        try:
            return fn(*args)
        finally:
            for ledger in ledgers:
                mine.remove(ledger)
    return run


def llm_configured() -> bool:
    try:
        return bool(llm._api_key())
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------ revision support --
def revision_notes(result: Review) -> list[str]:
    """The writer's notes for every unresolved bullet: the issue and the
    evidence, never a replacement fact."""
    notes = []
    for b in result.bullets:
        if b.verdict in (FAIL, WARN):
            notes.append(
                f'Bullet "{b.rewritten}" has a problem: {b.issue or b.issue_type}. '
                f'Its only support is: "{b.evidence or b.source or "nothing on file"}". '
                "Rewrite that bullet so every claim, number, tool and verb is in that support; "
                "if it cannot be, keep the support's own wording. Change no other bullet.")
    return notes


def revert(tailored_tex: str, result: Review, indexes: list[int]) -> str:
    """Put the master's own bullet back in place of each rewritten one. A
    bullet with no master original (none was paired) is left as it is."""
    tex = tailored_tex
    for b in result.bullets:
        if b.index in indexes and b.rewritten_tex and b.original_tex and b.rewritten_tex in tex:
            tex = tex.replace(b.rewritten_tex, b.original_tex, 1)
    return tex


_TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(text: str) -> str:
    """The person's plain text as LaTeX, so an edit cannot break the compile."""
    return "".join(_TEX_SPECIAL.get(c, c) for c in " ".join(text.split()))


def edit(tailored_tex: str, result: Review, edits: dict[int, str]) -> str:
    """Put the person's own wording in place of each bullet in `edits`."""
    tex = tailored_tex
    for b in result.bullets:
        if b.index in edits and b.rewritten_tex and b.rewritten_tex in tex:
            tex = tex.replace(b.rewritten_tex, tex_escape(edits[b.index]), 1)
    return tex


# The person's edits outlive the folder they were made in: a later re-tailor
# or correction round must not silently put a model's wording back over them.
# Keyed by job, matched by the master bullet they replace.
def _locks_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_BULLET_LOCKS") or paths.DATA / "bullet_locks.json")


def locks(job_id: str) -> list[dict]:
    try:
        return json.loads(_locks_path().read_text()).get(job_id, [])
    except (OSError, ValueError):
        return []


def add_locks(job_id: str, rows: list[dict]) -> None:
    path = _locks_path()
    with _cache_lock:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        kept = [r for r in data.get(job_id, []) if r["original"] not in {x["original"] for x in rows}]
        data[job_id] = kept + rows
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)


def apply_locks(tailored_tex: str, result: Review, job_id: str) -> tuple[str, list[int]]:
    """Put back every bullet the person edited for this job, matched by the
    master bullet it came from. Returns the text and the indexes it changed."""
    held = {row["original"]: row["text"] for row in locks(job_id)}
    tex, changed = tailored_tex, []
    for b in result.bullets:
        text = held.get(b.original)
        if text and b.rewritten_tex and b.rewritten_tex in tex and " ".join(b.rewritten.split()) != " ".join(text.split()):
            tex = tex.replace(b.rewritten_tex, tex_escape(text), 1)
            changed.append(b.index)
    return tex, changed


def save(app_dir: Path, result: Review) -> None:
    from archive import store
    store.write(app_dir, FILE, json.dumps(result.to_dict(), indent=2) + "\n")


FILE = "bullet_review.json"


def load(app_dir: Path) -> Optional[Review]:
    """The saved review, or None. A folder from before this existed has none."""
    try:
        return Review.from_dict(json.loads((Path(app_dir) / FILE).read_text()))
    except (OSError, ValueError, TypeError):
        return None
