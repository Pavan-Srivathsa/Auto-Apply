"""What the model calls cost, by provider, for whoever is counting.

`track()` opens a ledger for the code inside it; `record` adds to every
open ledger. The resume reviewer and its benchmark use it to say what a
reviewed resume cost; nothing else has to. Nothing here carries a key or a
request body, only token counts and dollars.

Prices are per million tokens. Jev's is TypeSafe's published price ($0.042
input, output free, docs.typesafe.ai/models, 2026-10-22) and can be changed
with `JEV_PRICE_IN` / `JEV_PRICE_OUT`. OpenRouter reports its own cost on
each reply (`usage.cost`), so its calls need no table.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Iterator, Optional

JEV_PRICE_IN = 0.042
JEV_PRICE_OUT = 0.0


@dataclass
class Line:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0


@dataclass
class Ledger:
    lines: dict[str, Line] = field(default_factory=dict)

    def add(self, provider: str, input_tokens: int, output_tokens: int, usd: float, seconds: float) -> None:
        line = self.lines.setdefault(provider, Line())
        line.calls += 1
        line.input_tokens += input_tokens
        line.output_tokens += output_tokens
        line.usd += usd
        line.seconds += seconds

    @property
    def usd(self) -> float:
        return sum(line.usd for line in self.lines.values())

    def to_dict(self) -> dict:
        return {"usd": round(self.usd, 6),
                "by_provider": {name: asdict(line) | {"usd": round(line.usd, 6)}
                                for name, line in self.lines.items()}}


_local = threading.local()


def _open() -> list[Ledger]:
    if not hasattr(_local, "ledgers"):
        _local.ledgers = []
    return _local.ledgers


@contextmanager
def track() -> Iterator[Ledger]:
    ledger = Ledger()
    _open().append(ledger)
    try:
        yield ledger
    finally:
        _open().remove(ledger)


def _price(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def jev_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _price("JEV_PRICE_IN", JEV_PRICE_IN)
            + output_tokens * _price("JEV_PRICE_OUT", JEV_PRICE_OUT)) / 1_000_000


def record(provider: str, input_tokens: int = 0, output_tokens: int = 0,
           usd: Optional[float] = None, seconds: float = 0.0) -> None:
    if usd is None:
        usd = jev_usd(input_tokens, output_tokens) if provider == "jev" else 0.0
    for ledger in _open():
        ledger.add(provider, int(input_tokens or 0), int(output_tokens or 0), float(usd or 0.0), seconds)
    try:
        _persist(provider, int(input_tokens or 0), int(output_tokens or 0), float(usd or 0.0))
    except OSError:
        pass


_file_lock = threading.Lock()


def usage_path():
    from pathlib import Path
    import paths
    return Path(os.environ.get("AUTOPILOT_USAGE") or paths.DATA / "usage.json")


def _persist(provider: str, input_tokens: int, output_tokens: int, usd: float) -> None:
    """Calls, tokens and dollars per day and provider, for the System view.
    Counts only: never a key, a prompt or a reply."""
    import json
    from datetime import date
    path = usage_path()
    with _file_lock:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        row = data.setdefault(date.today().isoformat(), {}).setdefault(provider, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
        row["calls"] += 1
        row["in"] += input_tokens
        row["out"] += output_tokens
        row["usd"] = round(row["usd"] + usd, 6)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)


def usage(days: int = 30) -> dict:
    import json
    from datetime import date, timedelta
    try:
        data = json.loads(usage_path().read_text())
    except (OSError, ValueError):
        data = {}
    since = (date.today() - timedelta(days=days)).isoformat()
    totals: dict = {}
    for day, providers in data.items():
        if day < since:
            continue
        for name, row in providers.items():
            t = totals.setdefault(name, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
            for k in ("calls", "in", "out"):
                t[k] += row[k]
            t["usd"] = round(t["usd"] + row["usd"], 6)
    return {"days": days, "by_provider": totals, "usd": round(sum(t["usd"] for t in totals.values()), 4),
            "by_day": {d: round(sum(r["usd"] for r in p.values()), 4) for d, p in sorted(data.items()) if d >= since}}
