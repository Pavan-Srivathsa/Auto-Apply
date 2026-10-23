"""Jev, TypeSafe's decision model, over its HTTP API.

Jev does not write. It answers narrow typed questions about a `state` with
calibrated probabilities: `noul` (yes/no, 0..1), `choice` (one of named
options) and `score` (a position on an ordered rubric). That is the judge
the loop wants: stable, cheap, fast, and never prose to parse. Text models
still write; Jev decides whether what they wrote holds up.

Optional by design. Without a key `configured()` is false and every
caller keeps its text-model path, so the app works the same before the key
is added. The key is read as `JEV_API_KEY` (with `JEV_BASE_URL`,
`JEV_MODEL`), or by the SDK's own names (`TYPESAFE_*`).
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx

API_KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL_ENV = "TYPESAFE_BASE_URL"
MODEL_ENV = "TYPESAFE_DEFAULT_MODEL"
ALIASES = {API_KEY_ENV: "JEV_API_KEY", BASE_URL_ENV: "JEV_BASE_URL", MODEL_ENV: "JEV_MODEL"}
BASE_URL = "https://api.typesafe.ai"
MODEL = "jev-latest"
TIMEOUT = 60.0
LONG_STATE = 24000     # characters of state past which the long-context header is sent


class JevError(RuntimeError):
    pass


def _env(name: str) -> str:
    """`JEV_*` first, as the person's .env names them, then the SDK's name."""
    return (os.environ.get(ALIASES[name]) or os.environ.get(name) or "").strip().strip('"').strip("'")


def configured() -> bool:
    return bool(_env(API_KEY_ENV))


def noul(instructions, criteria: Optional[dict] = None) -> dict:
    question = {"type": "noul", "instructions": instructions}
    if criteria:
        question["criteria"] = criteria
    return question


def choice(instructions, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions, criteria: list) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


RETRIES = 3            # attempts for a transient failure (network, 429, 5xx)
BACKOFF = (0.5, 1.5)   # seconds to wait before the second and third attempt
MAX_STATE = 200_000    # characters; Jev's window is 64K tokens, so past this the call is refused here
TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504}


def _valid(question: dict, answer) -> Optional[str]:
    """Why `answer` is not a well-formed reply to `question`, else None. A
    malformed value never reaches a caller as though it were a decision."""
    if not isinstance(answer, dict):
        return "not an object"
    kind = question.get("type")
    if kind == "noul":
        value = answer.get("noul")
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            return f"noul {value!r} is not a probability"
    elif kind == "score":
        value = answer.get("score")
        top = len(question.get("criteria") or []) - 1
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= max(top, 0):
            return f"score {value!r} is outside 0..{top}"
        probs = answer.get("probabilities")
        if probs is not None and (not isinstance(probs, dict)
                                  or abs(sum(float(v) for v in probs.values()) - 1.0) > 0.05):
            return "probabilities do not sum to one"
    elif kind == "choice":
        options = set((question.get("criteria") or {}).keys())
        if options and answer.get("choice") not in options:
            return f"choice {answer.get('choice')!r} is not one of {sorted(options)}"
    return None


def ask(state, questions: dict, model: Optional[str] = None,
        client: Optional[httpx.Client] = None) -> dict:
    """Every answer by question name, as the API returns it. Raises JevError.

    A transient failure is retried (`RETRIES`, `BACKOFF`); a reply whose
    values have the wrong shape raises rather than pass as a decision; a
    state too large for Jev's window is refused before it is sent. Errors
    never carry the key: only the status and the start of the reply body.
    """
    import time
    from . import costs

    key = _env(API_KEY_ENV)
    if not key:
        raise JevError(f"{ALIASES[API_KEY_ENV]} is not set")
    size = len(json.dumps(state))
    if size > MAX_STATE:
        raise JevError(f"state is {size} characters; Jev reads at most about {MAX_STATE}")
    body = {"model": model or _env(MODEL_ENV) or MODEL, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if size > LONG_STATE:
        headers["TypeSafe-Danger-Zone-Long-Context"] = "true"
    url = (_env(BASE_URL_ENV) or BASE_URL).rstrip("/") + "/v1/systemone"
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        for attempt in range(RETRIES):
            if attempt:
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            try:
                response = http.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                if attempt == RETRIES - 1:
                    raise JevError(f"Jev unreachable: {type(exc).__name__}") from exc
                continue
            if response.status_code in TRANSIENT and attempt < RETRIES - 1:
                continue
            break
    finally:
        if own:
            http.close()
    if response.status_code != 200:
        raise JevError(f"Jev answered {response.status_code}: {response.text[:200]}")
    try:
        data = response.json()
        answers = data["answers"]
    except (ValueError, KeyError, TypeError) as exc:
        raise JevError(f"Jev reply has no answers: {response.text[:200]}") from exc
    usage = data.get("usage") or {}
    costs.record("jev", usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                 seconds=time.monotonic() - started)
    missing = set(questions) - set(answers)
    if missing:
        raise JevError(f"Jev left questions unanswered: {sorted(missing)}")
    bad = {name: why for name in questions if (why := _valid(questions[name], answers[name]))}
    if bad:
        raise JevError(f"Jev answered out of shape: {bad}")
    return answers
