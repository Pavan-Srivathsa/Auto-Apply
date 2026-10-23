"""Every listed role gets its fit by itself (2026-10-22: "Let it
automatically check the fit. I am not clicking another thing.").

A thread in the server walks the roles on every watch's list that have no
fit yet, works each out with `run._fit` (the routed master resume, the
stories, the brief, the qualification), and writes it onto the row under
the store's lock. Fits are cached by link in `data/fit_cache.json`: the same
posting on Lenny's and on the company's own board is worked out once. A
posting that cannot be read gets `unknown` rather than being tried on every
pass. `AUTOPILOT_FIT=0` turns it off.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import paths
from scout import Posting, store
from scout import filter as level

log = logging.getLogger("autopilot.scout.fit")

ENV = "AUTOPILOT_FIT"
WORKERS = 2            # fits at a time: each is a fetch, three cheap model calls and Jev
BATCH = 6              # rows taken per pass, so a check's new rows are not stuck behind 200 old ones
IDLE = 60.0            # seconds to wait when every listed role has a fit
BACKOFF = 900.0        # ... and when a whole pass failed: the model key is out of credit or down

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_cache_lock = threading.Lock()


def cache_path() -> Path:
    return Path(os.environ.get("AUTOPILOT_FIT_CACHE") or paths.DATA / "fit_cache.json")


def cached(url: str) -> Optional[dict]:
    with _cache_lock:
        try:
            return json.loads(cache_path().read_text()).get(url)
        except (OSError, ValueError):
            return None


def remember(url: str, fit: dict) -> None:
    with _cache_lock:
        path = cache_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        data[url] = fit
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        os.replace(tmp, path)


def pending() -> list[tuple[str, str, dict]]:
    """(watch id, company, row) for every listed role without a fit; the
    newest postings first, so what was just found is ready first."""
    rows = []
    for watch in store.watches():
        if not watch.enabled:
            continue
        for row in watch.listed:
            # A role older than a week is not worth a model call (2026-10-23).
            if not row.get("fit") and not level.too_old(row.get("posted", "")):
                rows.append((watch.id, row.get("company") or watch.company, row))
    rows.sort(key=lambda item: item[2].get("posted") or "", reverse=True)
    return rows


def fit_row(watch_id: str, company: str, row: dict) -> Optional[dict]:
    """The row's fit, written on it, or None when it could not be worked out
    now (nothing is written or cached, so the next pass tries again)."""
    from scout import run
    fit = cached(row["url"])
    if not fit:
        watch = store.get_watch(watch_id)
        years = watch.args.get("years_min") if watch else None
        fit = run._fit(Posting(id=row["id"], title=row["title"], url=row["url"], location=row.get("location", "")),
                       company, **({"years_min": years} if years else {}))
        if not fit:
            return None
        remember(row["url"], fit)
    store.set_listed_fit(watch_id, row["id"], fit)
    return fit


def one_pass(workers: int = WORKERS, batch: int = BATCH) -> int:
    """Work out up to `batch` fits. Returns how many were written."""
    rows = pending()[:batch]
    if not rows:
        return 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(lambda item: _safe(*item), rows))
    return sum(1 for fit in done if fit)


def _safe(watch_id: str, company: str, row: dict) -> Optional[dict]:
    try:
        return fit_row(watch_id, company, row)
    except Exception:  # noqa: BLE001 - one bad row never stops the rest
        log.exception("fit %s", row.get("url"))
        return None


def _loop() -> None:
    while not _stop.is_set():
        try:
            written = one_pass()
        except Exception:  # noqa: BLE001
            log.exception("fit pass failed")
            written = 0
        if not written:
            # Nothing written: either every row has a fit, or every try failed
            # (an exhausted model key). Waiting longer in the second case keeps
            # it from hammering a refusing API.
            _stop.wait(IDLE if not pending() else BACKOFF)


def start() -> bool:
    global _thread
    if os.environ.get(ENV, "1") == "0" or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="autopilot-fit", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
