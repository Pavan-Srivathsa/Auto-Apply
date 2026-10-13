"""A registry of master resumes, and the cheap router that picks one per job.

The full LaTeX files never go into every routing request.  Each resume gets a
small factual profile once, then a job is resolved by manual choice, an exact
target-role match, a cache hit, or one screen-tier model call.  Tailoring still
runs once, against the selected master only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import paths
from server.models import slugify

from . import layout, llm, prompts, quality
from .fetch import Posting

ROOT = paths.BASE / "resumes"
REGISTRY = Path(os.environ.get("AUTOPILOT_RESUME_REGISTRY",
                               paths.DATA / "resume_bases.json"))
CACHE = Path(os.environ.get("AUTOPILOT_RESUME_BASE_CACHE",
                            paths.DATA / "resume_base_cache.json"))
LEGACY_TEX = paths.BASE / "resume.tex"
LEGACY_PDF = paths.BASE / "resume.pdf"
DEFAULT_ID = "default"
MAX_MODEL_PROFILES = 8
MAX_OTHER_LABELS = 40

PROFILE_PROMPT = """You make a compact routing profile for one candidate resume.
Use only facts in the resume. Return one JSON object and nothing else:
{"summary":"one sentence describing its positioning","target_roles":["3-8 role titles"],
"domains":["short domain names"],"strengths":["specific evidenced strengths or stacks"]}
Do not evaluate the candidate, invent experience, or copy contact details."""

PICK_PROMPT = """Choose which candidate resume base is the best starting point for this
job. Compare the job's actual priorities with the factual routing profiles.
Return one JSON object and nothing else:
{"base":"one supplied id","confidence":"high|medium|low","reason":"one short sentence"}
Choose exactly one supplied id. Prefer direct evidence and role alignment over keyword count.
Low confidence is correct when the profiles are equally relevant."""


@dataclass(frozen=True)
class ResumeBase:
    id: str
    label: str
    description: str
    directory: Path
    active: bool = True
    summary: str = ""
    target_roles: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()

    @property
    def tex_path(self) -> Path:
        return self.directory / "resume.tex"

    @property
    def pdf_path(self) -> Path:
        return self.directory / "resume.pdf"

    def public(self) -> dict:
        version = self.pdf_path.stat().st_mtime_ns if self.pdf_path.exists() else 0
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "active": self.active,
            "summary": self.summary,
            "target_roles": list(self.target_roles),
            "domains": list(self.domains),
            "strengths": list(self.strengths),
            "compiled": self.pdf_path.exists(),
            "version": version,
            "path": str(self.tex_path),
        }


@dataclass(frozen=True)
class Choice:
    base: str
    source: str
    confidence: str
    reason: str
    model: str = ""
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _read_json(path: Path, fallback: dict) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else fallback
    except (json.JSONDecodeError, OSError):
        return fallback


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def _legacy() -> Optional[ResumeBase]:
    if not LEGACY_TEX.exists():
        return None
    return ResumeBase(
        id=DEFAULT_ID,
        label="Primary resume",
        description="The original master resume.",
        directory=paths.BASE,
        summary="The original master resume.",
    )


def _row(value: dict) -> Optional[ResumeBase]:
    base_id = slugify(str(value.get("id", "")), 40)
    directory = ROOT / base_id
    if not (directory / "resume.tex").exists():
        return None
    return ResumeBase(
        id=base_id,
        label=str(value.get("label") or base_id),
        description=str(value.get("description") or ""),
        directory=directory,
        active=bool(value.get("active", True)),
        summary=str(value.get("summary") or ""),
        target_roles=tuple(str(item) for item in value.get("target_roles", []) if str(item)),
        domains=tuple(str(item) for item in value.get("domains", []) if str(item)),
        strengths=tuple(str(item) for item in value.get("strengths", []) if str(item)),
    )


def _data() -> dict:
    return _read_json(REGISTRY, {"primary": DEFAULT_ID, "bases": []})


def all_bases(active_only: bool = False) -> list[ResumeBase]:
    data = _data()
    rows = [row for value in data.get("bases", []) if (row := _row(value))]
    if not rows:
        legacy = _legacy()
        rows = [legacy] if legacy else []
    return [row for row in rows if row.active] if active_only else rows


def primary_id() -> str:
    rows = all_bases(active_only=True)
    if not rows:
        raise FileNotFoundError("no master resume is configured")
    wanted = str(_data().get("primary") or "")
    return next((row.id for row in rows if row.id == wanted), rows[0].id)


def get(base_id: Optional[str] = None, active_only: bool = False) -> ResumeBase:
    wanted = base_id or primary_id()
    row = next((row for row in all_bases(active_only=active_only)
                if row.id == wanted), None)
    if row is None:
        raise KeyError(f"no resume base called {wanted!r}")
    return row


def registry_hash() -> str:
    digest = hashlib.sha256()
    for row in all_bases(active_only=True):
        digest.update(row.id.encode())
        digest.update(row.tex_path.read_bytes())
        digest.update(json.dumps(row.public(), sort_keys=True).encode())
    return digest.hexdigest()[:20]


def _materialise() -> dict:
    data = _data()
    if data.get("bases"):
        return data
    legacy = _legacy()
    if legacy is not None:
        target = ROOT / DEFAULT_ID
        target.mkdir(parents=True, exist_ok=True)
        for path in paths.BASE.iterdir():
            if path.is_file() and path.name in ("resume.tex", "resume.pdf"):
                shutil.copy2(path, target / path.name)
            elif path.is_file() and path.suffix in (".cls", ".sty"):
                shutil.copy2(path, target / path.name)
        data = {
            "primary": DEFAULT_ID,
            "bases": [{
                "id": DEFAULT_ID,
                "label": "Primary resume",
                "description": "The original master resume.",
                "active": True,
                "summary": "The original master resume.",
                "target_roles": [],
                "domains": [],
                "strengths": [],
            }],
        }
        _write_json(REGISTRY, data)
    return data


def _json_reply(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model returned no JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model returned the wrong shape")
    return value


def make_profile(tex: str, label: str, description: str = "",
                 model: Optional[str] = None) -> dict:
    """One compact factual profile. A local fallback keeps upload non-fatal."""
    visible = layout.visible(tex)
    try:
        reply = llm.complete(
            prompts.text("resume_base.profile"),
            f"Label: {label}\nUser description: {description or '(none)'}\n\n"
            f"Resume:\n{visible[:18_000]}",
            model=model or derive_model(),
            temperature=0,
            max_tokens=500,
            reasoning=llm.minimal_reasoning(model or derive_model()),
        )
        value = _json_reply(reply)
        return {
            "summary": str(value.get("summary") or description or label).strip(),
            "target_roles": _short_list(value.get("target_roles")),
            "domains": _short_list(value.get("domains")),
            "strengths": _short_list(value.get("strengths")),
        }
    except Exception:  # noqa: BLE001 - upload must work when the profile call does not
        terms = sorted(set(re.findall(r"\b[A-Z][A-Za-z0-9+#.-]{1,24}\b", visible)))
        return {
            "summary": description or label,
            "target_roles": [label],
            "domains": [],
            "strengths": terms[:12],
        }


def _short_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:80] for item in value if str(item).strip()][:16]


def save_base(base_id: Optional[str], label: str, description: str, tex: str,
              compiled_pdf: Path) -> ResumeBase:
    data = _materialise()
    existing = {_row(value).id: value for value in data.get("bases", [])
                if _row(value) is not None}
    generated = slugify(base_id or label or "resume", 40)
    if base_id is None:
        root = generated
        suffix = 2
        while generated in existing:
            generated = f"{root}-{suffix}"
            suffix += 1
    elif generated not in existing:
        generated = slugify(base_id, 40)

    directory = ROOT / generated
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "resume.tex"
    if target.exists():
        history = directory / ".history"
        history.mkdir(exist_ok=True)
        stamp = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        shutil.copy2(target, history / f"{stamp}.tex")
    target.write_text(tex)
    shutil.copy2(compiled_pdf, directory / "resume.pdf")
    old = existing.get(generated, {})
    profile = make_profile(tex, label or old.get("label") or generated, description)
    row = {
        "id": generated,
        "label": (label or old.get("label") or generated).strip(),
        "description": description.strip(),
        "active": bool(old.get("active", True)),
        **profile,
    }
    values = [value for value in data.get("bases", [])
              if slugify(str(value.get("id", "")), 40) != generated]
    values.append(row)
    data["bases"] = values
    if not data.get("primary") or data["primary"] not in {v["id"] for v in values}:
        data["primary"] = generated
    _write_json(REGISTRY, data)
    return get(generated)


def set_primary(base_id: str) -> None:
    row = get(base_id, active_only=True)
    data = _materialise()
    data["primary"] = row.id
    _write_json(REGISTRY, data)


def set_active(base_id: str, active: bool) -> None:
    data = _materialise()
    found = False
    for value in data.get("bases", []):
        if slugify(str(value.get("id", "")), 40) == base_id:
            value["active"] = active
            found = True
    if not found:
        raise KeyError(base_id)
    if not active and sum(bool(v.get("active", True)) for v in data["bases"]) < 1:
        raise ValueError("at least one resume must stay active")
    _write_json(REGISTRY, data)
    if not active and data.get("primary") == base_id:
        set_primary(all_bases(active_only=True)[0].id)


def delete_base(base_id: str) -> None:
    """Permanently remove an archived base and its local source history."""
    row = get(base_id)
    if row.active:
        raise ValueError("archive this resume before deleting it")
    data = _materialise()
    data["bases"] = [
        value for value in data.get("bases", [])
        if slugify(str(value.get("id", "")), 40) != row.id
    ]
    _write_json(REGISTRY, data)
    if row.directory.exists():
        shutil.rmtree(row.directory)


def derive_model() -> str:
    return os.environ.get("OPENROUTER_SCREEN_MODEL", "deepseek/deepseek-v4-flash")


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9+#.]{3,}", text.lower())}


def _rank(row: ResumeBase, posting: Posting) -> int:
    wanted = _words(posting.title + "\n" + quality.clean(posting.text)[:8_000])
    profile = " ".join((row.label, row.description, row.summary,
                        *row.target_roles, *row.domains, *row.strengths))
    return len(wanted & _words(profile))


def _exact_role(rows: list[ResumeBase], title: str) -> Optional[ResumeBase]:
    title_words = " ".join(re.findall(r"[a-z0-9+#.]+", title.lower()))
    matches = []
    for row in rows:
        roles = [" ".join(re.findall(r"[a-z0-9+#.]+", role.lower()))
                 for role in row.target_roles]
        if any(role and (role == title_words or role in title_words) for role in roles):
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def _canonical(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query)
    kept = [(key, value) for key, value in query
            if not key.lower().startswith("utm_") and key.lower() not in {"jr_id", "gh_src"}]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path,
                                    urllib.parse.urlencode(kept), ""))


def _cache_keys(posting: Posting) -> tuple[str, str]:
    version = registry_hash()
    url = hashlib.sha256((version + "\0" + _canonical(posting.url)).encode()).hexdigest()
    text = hashlib.sha256(
        (version + "\0" + str(quality.simhash(quality.clean(posting.text)))).encode()
    ).hexdigest()
    return f"url:{url}", f"text:{text}"


def _cached(posting: Posting) -> Optional[Choice]:
    data = _read_json(CACHE, {"entries": {}, "similar": []})
    entries = data.get("entries", {})
    for key in _cache_keys(posting):
        value = entries.get(key)
        if value and any(row.id == value.get("base") for row in all_bases(active_only=True)):
            return Choice(
                base=value["base"], source="cache",
                confidence=value.get("confidence", "medium"),
                reason=value.get("reason", "A matching posting used this resume."),
                model=value.get("model", ""), cached=True,
            )
    fingerprint = quality.simhash(quality.clean(posting.text))
    version = registry_hash()
    for value in reversed(data.get("similar", [])):
        if value.get("registry") != version:
            continue
        try:
            near = quality.distance(fingerprint, int(value.get("simhash"))) <= 6
        except (TypeError, ValueError):
            near = False
        if near and any(row.id == value.get("base") for row in all_bases(active_only=True)):
            return Choice(
                base=value["base"], source="cache",
                confidence=value.get("confidence", "medium"),
                reason=value.get("reason", "A near-identical posting used this resume."),
                model=value.get("model", ""), cached=True,
            )
    return None


def _cache(posting: Posting, choice: Choice) -> None:
    data = _read_json(CACHE, {"entries": {}, "similar": []})
    entries = data.setdefault("entries", {})
    value = choice.to_dict()
    for key in _cache_keys(posting):
        entries[key] = value
    similar = data.setdefault("similar", [])
    similar.append(value | {
        "registry": registry_hash(),
        "simhash": quality.simhash(quality.clean(posting.text)),
    })
    data["similar"] = similar[-500:]
    _write_json(CACHE, data)


def choose(posting: Posting, requested: Optional[str] = None) -> Choice:
    """Resolve one active base. Failure always falls back to the primary."""
    rows = all_bases(active_only=True)
    if not rows:
        raise FileNotFoundError("no master resume is configured")
    if requested:
        try:
            row = get(requested, active_only=True)
            return Choice(row.id, "manual", "high", "Chosen for this job.")
        except KeyError:
            pass
    if len(rows) == 1:
        return Choice(rows[0].id, "only", "high", "Only active resume.")
    exact = _exact_role(rows, posting.title)
    if exact:
        choice = Choice(exact.id, "rule", "high",
                        f'Its target roles uniquely match "{posting.title}".')
        _cache(posting, choice)
        return choice
    cached = _cached(posting)
    if cached:
        return cached

    ranked = sorted(rows, key=lambda row: (-_rank(row, posting), row.id))
    detailed = ranked[:MAX_MODEL_PROFILES]
    compact = [
        {
            "id": row.id, "label": row.label, "description": row.description,
            "summary": row.summary, "target_roles": list(row.target_roles),
            "domains": list(row.domains), "strengths": list(row.strengths),
        }
        for row in detailed
    ]
    if len(ranked) > len(detailed):
        compact.extend({
            "id": row.id, "label": row.label,
            "target_roles": list(row.target_roles),
        } for row in ranked[len(detailed):len(detailed) + MAX_OTHER_LABELS])
    model = derive_model()
    try:
        reply = llm.complete(
            prompts.text("resume_base.pick"),
            "## Resume bases\n\n" + json.dumps(compact, indent=2)
            + "\n\n## Job\n\n" + posting.to_markdown()[:14_000],
            model=model, temperature=0, max_tokens=180,
            reasoning=llm.minimal_reasoning(model),
        )
        value = _json_reply(reply)
        base_id = str(value.get("base") or "")
        if base_id not in {row["id"] for row in compact}:
            raise ValueError("model chose an unknown resume")
        confidence = str(value.get("confidence") or "low").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        choice = Choice(
            base_id, "model", confidence,
            str(value.get("reason") or "Closest routing profile.")[:240],
            model=model,
        )
        _cache(posting, choice)
        return choice
    except Exception as exc:  # noqa: BLE001 - routing must not fail a job
        primary = get(primary_id(), active_only=True)
        return Choice(primary.id, "fallback", "low",
                      f"Router failed; used the primary resume ({type(exc).__name__}).")


def copy_assets(base: ResumeBase, destination: Path) -> None:
    for sibling in base.directory.iterdir():
        if sibling.is_file() and sibling.suffix in (".cls", ".sty"):
            shutil.copy2(sibling, destination / sibling.name)
