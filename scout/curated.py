"""The curated watchlist: fifty agentic-AI companies the person chose, each
resolved to its public ATS board and watched like any other.

Separate from the person's own watches on purpose. The registry
(`data/curated_registry.json`) records, per company, the board it resolved
to, how that was verified, and the watch id; a company whose board is
already watched by hand is linked to that watch and not watched twice. Every
curated watch holds (`hold`): its roles are listed and scored, never queued
on their own. The first check seeds as Scout always does (no hits, no
mail), and every open role at the level is listed at once, so the initial
inventory is the curated watches' listed roles, ranked by fit.

A board counts only when it can be shown to be the company's: Greenhouse
names its board; for Ashby and Lever the postings must name the company.
A company that resolves to nothing is reported, not guessed.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

import paths

TAG = "agentic-ai-50"
COMPANIES = [
    "LlamaIndex", "LangChain", "Decagon", "Hebbia", "Browserbase", "Factory AI", "Sierra", "Glean", "Fireworks AI",
    "Arcade", "Deepgram", "Relevance AI", "Gumloop", "Cognition", "Rogo", "Retell AI", "Vapi", "ElevenLabs",
    "Harvey", "Runpod", "Hightouch", "Daytona", "Ambience Healthcare", "Abridge", "Ema", "Dust", "Composio", "E2B",
    "Baseten", "Modal", "Unstructured", "Langfuse", "Arize AI", "Galileo", "Braintrust", "Weaviate", "Pinecone",
    "Exa", "Tavily", "Cartesia", "LiveKit", "Bland AI", "Synthflow AI", "Lindy", "11x", "Artisan", "Clay",
    "Legora", "Hippocratic AI", "Poolside",
]
# Board slugs that are not the name lowercased (checked live, 2026-10-22).
HINTS = {"Factory AI": ["factory", "factoryai"], "Fireworks AI": ["fireworksai", "fireworks-ai", "fireworks"],
         "Relevance AI": ["relevanceai", "relevance-ai"], "Retell AI": ["retellai", "retell-ai"],
         "Ambience Healthcare": ["ambiencehealthcare", "ambience"], "Arize AI": ["arizeai", "arize-ai", "arize"],
         "Bland AI": ["bland", "blandai"], "Synthflow AI": ["synthflow", "synthflowai"],
         "Hippocratic AI": ["hippocraticai", "hippocratic-ai"], "Cognition": ["cognition", "cognitionai"],
         "Modal": ["modal", "modallabs"], "Sierra": ["sierra", "sierraai"], "Ema": ["ema", "emaunlimited"],
         "Dust": ["dust", "dusttt"], "Exa": ["exa", "exaai"], "Clay": ["clay", "claylabs"],
         "Galileo": ["rungalileo", "galileo"], "Glean": ["gleanwork", "glean"], "Arcade": ["arcade", "arcadedev", "arcade-ai"],
         "Artisan": ["artisan", "artisanai"], "Rogo": ["rogo", "rogodata"], "Lindy": ["lindy", "lindyai"],
         "Harvey": ["harvey", "harveyai"], "Vapi": ["vapi"], "Legora": ["legora", "leya"]}
# Boards found by hand (2026-10-22) where the name is not the slug, or the
# company hires through a parent or an ATS the guesses do not reach.
# `titles`: keep only roles whose title has one of these words (a parent's
# board lists the parent's roles too).
OVERRIDES = {
    "Hightouch": {"provider": "ashby", "slug": "hightouch-inc", "url": "https://jobs.ashbyhq.com/hightouch-inc"},
    "Hippocratic AI": {"provider": "ashby", "slug": "hippocratic ai", "url": "https://jobs.ashbyhq.com/hippocratic%20ai"},
    "Langfuse": {"provider": "ashby", "slug": "clickhouse", "url": "https://jobs.ashbyhq.com/clickhouse?for=langfuse",
                 "parent": "ClickHouse", "titles": ["langfuse"]},
    "11x": {"provider": "gem", "slug": "11x-ai", "url": "https://jobs.gem.com/11x-ai"},
    "Lindy": {"provider": "teamtailor", "feed": "https://careers.lindy.ai/jobs.rss", "url": "https://careers.lindy.ai/jobs.rss"},
}
# No board Scout can read; said on the page instead of guessed.
UNSUPPORTED = {
    "Daytona": "posts its roles as Notion pages (daytona.io/careers); add roles by hand",
    "Synthflow AI": "hires through Deel (jobs.deel.com/synthflow-ai), mostly in Berlin",
    "Galileo": "now part of Cisco; its roles are on Cisco's careers site",
}

# The roles the person is after, ahead of the general Scout defaults.
POSITIVE = ["applied ai", "ai engineer", "llm", "agent", "machine learning", "ml engineer", "software engineer",
            "backend", "inference", "evaluation", "forward deployed", "solutions engineer", "member of technical staff",
            "research engineer", "platform engineer", "full stack", "founding engineer"]
YEARS_MIN = 6           # a 4-5 year ask is judged by the fit model on these watches, not ruled out in code
TIMEOUT = 15.0


def registry_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_CURATED") or paths.DATA / "curated_registry.json")


def registry() -> dict:
    try:
        return json.loads(registry_path().read_text())
    except (OSError, ValueError):
        return {"tag": TAG, "companies": {}}


def _save(data: dict) -> None:
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(target)


def _core(name: str) -> str:
    return re.sub(r"\b(ai|inc|labs|healthcare)\b", "", name.lower()).strip()


def slugs(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    out = HINTS.get(name, []) + [base, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                                  re.sub(r"[^a-z0-9]", "", _core(name)), base + "ai", base + "hq"]
    return list(dict.fromkeys(s for s in out if s))


def _mentions(name: str, texts: list[str]) -> bool:
    core = re.sub(r"[^a-z0-9 ]", "", _core(name)).strip()
    return bool(core) and any(re.search(rf"\b{re.escape(core)}\b", (t or "").lower()) for t in texts)


# A board named like the company is not enough for a common name: the
# Greenhouse board called "Galileo" hires dermatologists. Every company on
# this list builds AI, so its postings say so somewhere.
AI_CONTEXT = re.compile(r"\b(ai|a\.i\.|machine learning|ml|llms?|agents?|agentic|models?|inference|"
                        r"voice ai|genai|artificial intelligence)\b", re.I)


def _about_ai(texts: list[str]) -> bool:
    return any(AI_CONTEXT.search(t or "") for t in texts)


def _greenhouse(name: str, slug: str, http: httpx.Client) -> Optional[dict]:
    r = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    if r.status_code != 200:
        return None
    board = r.json().get("name", "")
    if not _mentions(name, [board]):
        return None
    jobs = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true").json().get("jobs", [])
    if not _about_ai([j.get("title", "") + " " + (j.get("content") or "")[:4000] for j in jobs[:8]]):
        return None
    return {"provider": "greenhouse", "slug": slug, "url": f"https://job-boards.greenhouse.io/{slug}",
            "verified_by": f"Greenhouse names the board '{board}'", "open_roles": len(jobs)}


def _ashby(name: str, slug: str, http: httpx.Client) -> Optional[dict]:
    r = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs", []) or []
    texts = [j.get("descriptionPlain", "")[:4000] for j in jobs[:8]]
    if not jobs or not _mentions(name, texts) or not _about_ai(texts):
        return None
    return {"provider": "ashby", "slug": slug, "url": f"https://jobs.ashbyhq.com/{slug}",
            "verified_by": "its Ashby postings name the company", "open_roles": len(jobs)}


def _lever(name: str, slug: str, http: httpx.Client) -> Optional[dict]:
    r = http.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if r.status_code != 200 or not isinstance(r.json(), list) or not r.json():
        return None
    jobs = r.json()
    texts = [j.get("descriptionPlain", "")[:4000] for j in jobs[:8]]
    if not _mentions(name, texts) or not _about_ai(texts):
        return None
    return {"provider": "lever", "slug": slug, "url": f"https://jobs.lever.co/{slug}",
            "verified_by": "its Lever postings name the company", "open_roles": len(jobs)}


def resolve(name: str, http: Optional[httpx.Client] = None, careers_pages: bool = True) -> dict:
    """The company's board, verified, or the slugs tried and why none fit."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if name in OVERRIDES:
        o = OVERRIDES[name]
        return {"company": name, "status": "resolved", "open_roles": None, "checked_at": now,
                "verified_by": "found by hand" + (f": hires through {o['parent']}" if o.get("parent") else ""), **o}
    if name in UNSUPPORTED:
        return {"company": name, "status": "unsupported", "note": UNSUPPORTED[name], "checked_at": now, "tried": []}
    own = http is None
    http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Autopilot/1.0"})
    tried = []
    try:
        for slug in slugs(name):
            for probe in (_ashby, _greenhouse, _lever):
                try:
                    found = probe(name, slug, http)
                except (httpx.HTTPError, ValueError):
                    found = None
                tried.append(f"{probe.__name__[1:]}:{slug}")
                if found:
                    return {"company": name, "status": "resolved", **found,
                            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        # Its own careers page, which names the board it embeds.
        from scout import providers
        if not careers_pages:
            return {"company": name, "status": "unresolved", "tried": tried,
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "no public Greenhouse, Ashby or Lever board that names the company"}
        base = re.sub(r"[^a-z0-9]", "", _core(name)) or re.sub(r"[^a-z0-9]", "", name.lower())
        for page in (f"https://www.{base}.com/careers", f"https://{base}.ai/careers", f"https://www.{base}.ai/careers",
                     f"https://{base}.com/careers"):
            tried.append(f"careers page {page}")
            try:
                boards = providers.discover(page)
            except Exception:  # noqa: BLE001 - not this domain
                continue
            for board in boards:
                for probe, host in ((_ashby, "ashbyhq.com"), (_greenhouse, "greenhouse.io"), (_lever, "lever.co")):
                    if host in board:
                        slug = [part for part in board.split("/") if part][-1].split("?")[0]
                        try:
                            found = probe(name, slug, http)
                        except (httpx.HTTPError, ValueError):
                            found = None
                        if found:
                            return {"company": name, "status": "resolved", **found, "via": page,
                                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    finally:
        if own:
            http.close()
    return {"company": name, "status": "unresolved", "tried": tried,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "no public Greenhouse, Ashby or Lever board that names the company; add its careers page by hand"}


def import_all(companies: Optional[list[str]] = None, check: bool = True, workers: int = 8) -> dict:
    """Resolve every company, create one held watch per new board, link a
    board already watched by hand, and record it all in the registry.
    Idempotent: a second run creates nothing that exists."""
    from scout import Watch, run, store
    companies = companies or COMPANIES
    data = registry()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        resolved = list(pool.map(resolve, companies))
    existing = {(w.provider, w.args.get("slug") or w.args.get("feed")): w for w in store.watches()}
    created, linked = [], []
    for row in resolved:
        entry = data["companies"].get(row["company"], {}) | row
        if row["status"] == "resolved":
            board_key = (row["provider"], row.get("slug") or row.get("feed"))
            prior = existing.get(board_key)
            if prior is not None and row.get("titles") and not prior.args.get("curated"):
                prior = None            # a parent's board watched by hand is the parent's; this one filters to the company
            if prior is not None:
                entry["watch_id"], entry["manual"] = prior.id, not prior.args.get("curated")
                linked.append(row["company"])
            else:
                board = {k: row[k] for k in ("slug", "feed") if row.get(k)}
                watch = Watch(url=row["url"], company=row["company"], provider=row["provider"],
                              args=board | {"curated": TAG, "hold": True, "years_min": YEARS_MIN},
                              positive=row.get("titles") or POSITIVE)
                watch, made = store.add_watch(watch)
                entry["watch_id"], entry["manual"] = watch.id, False
                if made:
                    created.append(watch)
                existing[board_key] = watch
        data["companies"][row["company"]] = entry
    _save(data)
    checked = []
    if check:
        for watch in created:
            try:
                checked.append({"company": watch.company, **run.check(watch)})
            except Exception as exc:  # noqa: BLE001 - one board never stops the import
                checked.append({"company": watch.company, "error": str(exc)[:160]})
    return {"resolved": sum(1 for r in resolved if r["status"] == "resolved"), "of": len(resolved),
            "created": [w.company for w in created], "linked": linked,
            "unresolved": [r["company"] for r in resolved if r["status"] == "unresolved"],
            "unsupported": {r["company"]: r["note"] for r in resolved if r["status"] == "unsupported"}, "checked": checked}


def inventory() -> dict:
    """Every role the curated watches list now, ranked by fit, with each
    company's board health and last verification."""
    from scout import store
    data = registry()
    watches = {w.id: w for w in store.watches()}
    order = {"qualified": 0, "stretch": 1, "": 2, "unknown": 3, "not_qualified": 4}
    roles, companies = [], []
    for name, entry in sorted(data["companies"].items()):
        watch = watches.get(entry.get("watch_id", ""))
        companies.append({"company": name, "status": entry.get("status"), "provider": entry.get("provider", ""),
                          "url": entry.get("url", ""), "verified_by": entry.get("verified_by", ""),
                          "watch_id": entry.get("watch_id"), "manual": entry.get("manual", False),
                          "health": ("broken" if watch and watch.error else "ok" if watch and watch.last_ok else
                                     "not checked" if watch else entry.get("status")),
                          "error": watch.error if watch else entry.get("note", ""),
                          "last_verified": watch.last_ok if watch else None,
                          "listed": len(watch.listed) if watch else 0, "total": watch.last_total if watch else 0})
        for row in (watch.listed if watch else []):
            roles.append(row | {"company": name, "watch_id": watch.id})
    roles.sort(key=lambda r: (order.get((r.get("fit") or {}).get("verdict", ""), 2), r.get("posted", "") or ""), reverse=False)
    return {"tag": TAG, "companies": companies, "roles": roles}
