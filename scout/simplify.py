"""Simplify's New-Grad list (github.com/SimplifyJobs/New-Grad-Positions) as a
Scout source.

The repository publishes every listing as one JSON file (~13 MB, 19K rows);
it is read whole, once per check. Kept: active, visible, posted within
`args.days` (14), in `args.categories` (Software, AI/ML/Data). Each row's link
is the employer's own application, so a role is screened, scored and queued
like any other. The list's sponsorship field says "Other" on every recent
row, so the visa check stays with the screen. A watch on it holds: it lists
and scores, the person adds (2026-10-23).
"""
from __future__ import annotations

import ast
import time
from datetime import datetime, timezone

import httpx

from scout import Posting, Watch

URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
CATEGORIES = ["Software", "AI/ML/Data"]
DAYS = 7          # past week only (2026-10-23)


def _true(value) -> bool:
    return str(value).strip().lower() == "true" or value is True


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = ast.literal_eval(str(value))
        return [str(v) for v in parsed] if isinstance(parsed, (list, tuple)) else [str(parsed)]
    except (ValueError, SyntaxError):
        return [str(value)] if value else []


def parse(rows: list[dict], categories: list[str], days: int, now: float | None = None) -> list[Posting]:
    now = now or time.time()
    out = []
    for r in rows:
        try:
            posted = float(r.get("date_posted") or 0)
        except (TypeError, ValueError):
            continue
        if not (_true(r.get("active")) and _true(r.get("is_visible", True))) or now - posted > days * 86400:
            continue
        if categories and r.get("category") not in categories:
            continue
        out.append(Posting(id=str(r.get("id") or r.get("url")), title=str(r.get("title") or ""), url=str(r.get("url") or ""),
                           location=" / ".join(_list(r.get("locations")))[:200], company=str(r.get("company_name") or ""),
                           posted=datetime.fromtimestamp(posted, tz=timezone.utc).strftime("%Y-%m-%d")))
    out.sort(key=lambda p: p.posted, reverse=True)
    return out


def fetch(watch: Watch, http: httpx.Client) -> list[Posting]:
    args = watch.args or {}
    rows = http.get(args.get("feed") or URL, timeout=90).raise_for_status().json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Simplify's listings file came back empty; the repository may have moved it")
    return parse(rows, args.get("categories") or CATEGORIES, int(args.get("days") or DAYS))
