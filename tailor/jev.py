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


def ask(state, questions: dict, model: Optional[str] = None,
        client: Optional[httpx.Client] = None) -> dict:
    """Every answer by question name, as the API returns it. Raises JevError."""
    key = _env(API_KEY_ENV)
    if not key:
        raise JevError(f"{ALIASES[API_KEY_ENV]} is not set")
    body = {"model": model or _env(MODEL_ENV) or MODEL, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if len(json.dumps(state)) > LONG_STATE:
        headers["TypeSafe-Danger-Zone-Long-Context"] = "true"
    url = (_env(BASE_URL_ENV) or BASE_URL).rstrip("/") + "/v1/systemone"
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        response = http.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise JevError(f"Jev unreachable: {exc}") from exc
    finally:
        if own:
            http.close()
    if response.status_code != 200:
        raise JevError(f"Jev answered {response.status_code}: {response.text[:200]}")
    try:
        answers = response.json()["answers"]
    except (ValueError, KeyError) as exc:
        raise JevError(f"Jev reply has no answers: {response.text[:200]}") from exc
    missing = set(questions) - set(answers)
    if missing:
        raise JevError(f"Jev left questions unanswered: {sorted(missing)}")
    return answers
