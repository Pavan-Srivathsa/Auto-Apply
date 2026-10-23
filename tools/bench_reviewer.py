"""Benchmark the ML/DS bullet reviewer: Jev only, the reasoning model only,
and the hybrid, on the labeled pairs in bench/.

    uv run python tools/bench_reviewer.py [--calibrate] [--modes jev,llm,hybrid] [--model M]

Writes data/bench_results.json (everything) and bench/RESULTS.md (no private text). With --calibrate, the Jev
scores of the first pass choose `pass_at`, `fail_at` and `escalate_at`
(tailor/reviewer_thresholds.json), and the hybrid runs on those.

Definitions. A pair is *flagged* when its verdict is not PASS. False
acceptance: a `material` pair that PASSes. False rejection: an `ok` pair
that FAILs. Human review: an `ok` pair left at WARN or UNKNOWN. `lossy`
pairs are reported, never scored.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

paths.load_env()

from bench.reviewer_pairs import rows  # noqa: E402
from tailor import costs, reviewer  # noqa: E402

PER_RESUME = 14          # bullets in a typical one-page resume, for the per-resume figures


def as_pairs(data: list[dict]) -> list[reviewer.Pair]:
    return [reviewer.Pair(i, r["rewritten"], r["source"], r["source"], "master") for i, r in enumerate(data)]


def run(mode: str, data: list[dict]) -> dict:
    started = time.monotonic()
    with costs.track() as ledger:
        out = reviewer.judge_pairs(as_pairs(data), mode=mode, escalate=(mode == "hybrid"), use_cache=False)
    return {"mode": mode, "seconds": time.monotonic() - started, "cost": ledger.to_dict(),
            "verdicts": [{"id": r["id"], "verdict": b.verdict, "method": b.method, "issue_type": b.issue_type,
                          "issue": b.issue, "confidence": b.confidence, "scores": b.scores,
                          "escalated": b.escalated, "unverified": b.unverified_claims}
                         for r, b in zip(data, out)]}


def metrics(data: list[dict], verdicts: list[dict]) -> dict:
    by = {v["id"]: v for v in verdicts}
    material = [r for r in data if r["label"] == "material"]
    ok = [r for r in data if r["label"] == "ok"]
    flagged = [r for r in data if by[r["id"]]["verdict"] != reviewer.PASS and r["label"] != "lossy"]
    true_flags = [r for r in flagged if r["label"] == "material"]
    false_accept = [r for r in material if by[r["id"]]["verdict"] == reviewer.PASS]
    false_reject = [r for r in ok if by[r["id"]]["verdict"] == reviewer.FAIL]
    human = [r for r in ok if by[r["id"]]["verdict"] in (reviewer.WARN, reviewer.UNKNOWN)]
    unknown = [r for r in data if by[r["id"]]["verdict"] == reviewer.UNKNOWN]
    categories = sorted({r["category"] for r in material})
    recall = {c: round(sum(1 for r in material if r["category"] == c and by[r["id"]]["verdict"] != reviewer.PASS)
                       / max(1, sum(1 for r in material if r["category"] == c)), 2) for c in categories}
    return {
        "n": len(data), "material": len(material), "ok": len(ok),
        "false_acceptance_rate": round(len(false_accept) / max(1, len(material)), 3),
        "false_rejection_rate": round(len(false_reject) / max(1, len(ok)), 3),
        "human_review_rate_on_ok": round(len(human) / max(1, len(ok)), 3),
        "precision": round(len(true_flags) / max(1, len(flagged)), 3),
        "recall": round(len(true_flags) / max(1, len(material)), 3),
        "fail_recall": round(sum(1 for r in material if by[r["id"]]["verdict"] == reviewer.FAIL)
                             / max(1, len(material)), 3),
        "unknown": len(unknown),
        "escalated": sum(1 for v in verdicts if v["escalated"]),
        "recall_by_category": recall,
        "false_accepts": [(r["id"], r["category"], r["rewritten"][:90]) for r in false_accept],
        "false_rejects": [(r["id"], by[r["id"]]["issue"][:90], r["rewritten"][:90]) for r in false_reject],
    }


def verdict_at(scores: dict, unverified_figures: bool, pass_at: float, fail_at: float) -> str:
    """The Jev-only verdict for one pair at these thresholds (mirrors reviewer._decide)."""
    if unverified_figures:
        return reviewer.FAIL
    p = min(scores[m] for m in reviewer.MATERIAL)
    if p < fail_at:
        return reviewer.FAIL
    return reviewer.PASS if p >= pass_at else reviewer.WARN


def calibrate(data: list[dict], jev_run: dict) -> dict:
    """Pick thresholds from the Jev pass. In order of the project's priorities:
    the lowest false acceptance, then the lowest false rejection, then the
    fewest correct bullets sent to a person. `escalate_at` is the lowest Jev
    P(accurate) any correct paraphrase received, so a Jev-only FAIL above it
    is checked by the reasoning model before it stands."""
    rows_by = {v["id"]: v for v in jev_run["verdicts"]}
    grid = [round(x / 100, 2) for x in range(5, 100, 5)]
    best = None
    for pass_at, fail_at in itertools.product(grid, grid):
        if fail_at > pass_at:
            continue
        verdicts = []
        for r in data:
            v = rows_by[r["id"]]
            figures = any(u.startswith("figure ") for u in v["unverified"])
            verdict = verdict_at(v["scores"], figures, pass_at, fail_at) if v["scores"] else v["verdict"]
            verdicts.append({**v, "verdict": verdict})
        m = metrics(data, verdicts)
        # Ties go to the stricter thresholds: the benchmark is small, and a
        # threshold at the edge of what it allowed is the one unseen data breaks.
        key = (m["false_acceptance_rate"], m["false_rejection_rate"], m["human_review_rate_on_ok"], -fail_at, pass_at)
        if best is None or key < best[0]:
            best = (key, pass_at, fail_at, m)
    _, pass_at, fail_at, m = best
    ok_scores = [min(rows_by[r["id"]]["scores"][k] for k in reviewer.MATERIAL)
                 for r in data if r["label"] == "ok" and rows_by[r["id"]]["scores"]]
    escalate_at = round(max(0.0, min(ok_scores) - 0.05), 2) if ok_scores else 0.1
    return {"pass_at": pass_at, "fail_at": fail_at, "escalate_at": escalate_at, "jev_only_at_these": m}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="jev,llm,hybrid")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--model", help="the reasoning model (OPENROUTER_REVIEW_MODEL)")
    parser.add_argument("--private", default=str(paths.DATA / "bench_private.jsonl"),
                        help="labeled pairs from real resumes, kept in the data folder (never the repository)")
    args = parser.parse_args()
    if args.model:
        os.environ["OPENROUTER_REVIEW_MODEL"] = args.model
    data = rows()
    private = Path(args.private)
    if private.exists():
        data += [json.loads(line) for line in private.read_text().splitlines() if line.strip()]
    results: dict = {"rubric": reviewer.RUBRIC_VERSION, "pairs": len(data),
                     "private_pairs": sum(1 for r in data if r["id"].startswith("r")), "review_model": reviewer.review_model(),
                     "date": time.strftime("%Y-%m-%d"), "runs": {}}
    modes = args.modes.split(",")
    # Calibrate on one half, score on the other, so the thresholds are never
    # judged on the pairs that chose them.
    calib = [r for n, r in enumerate(data) if n % 2 == 0]
    held = [r for n, r in enumerate(data) if n % 2 == 1]
    if args.calibrate:
        jev_run = run("jev", calib)
        cal = calibrate(calib, jev_run)
        results["calibration"] = cal
        reviewer.THRESHOLDS.write_text(json.dumps({
            "thresholds": {k: cal[k] for k in ("pass_at", "fail_at", "escalate_at")},
            "calibrated_on": f"bench/reviewer_pairs.py, the {len(calib)} even pairs of {len(data)}, {results['date']}",
            "rubric": reviewer.RUBRIC_VERSION,
            "jev_only_at_these": {k: v for k, v in cal["jev_only_at_these"].items()
                                  if k not in ("false_accepts", "false_rejects")}}, indent=2) + "\n")
        print("calibrated:", {k: cal[k] for k in ("pass_at", "fail_at", "escalate_at")})
    for mode in modes:
        r = run(mode, data)
        r["metrics"] = metrics(held, [v for v in r["verdicts"] if v["id"] in {h["id"] for h in held}])
        r["metrics_all"] = metrics(data, r["verdicts"])
        n = len(data)
        r["per_resume"] = {"usd": round(r["cost"]["usd"] / n * PER_RESUME, 5),
                           "seconds": round(r["seconds"] / n * PER_RESUME, 2)}
        results["runs"][mode] = r
        m = r["metrics"]
        print(f"{mode:7s} FAR {m['false_acceptance_rate']:.3f}  FRR {m['false_rejection_rate']:.3f}  "
              f"human {m['human_review_rate_on_ok']:.3f}  P {m['precision']:.3f}  R {m['recall']:.3f}  "
              f"esc {m['escalated']}  ${r['cost']['usd']:.4f}  {r['seconds']:.1f}s")
    if "jev" in results["runs"]:
        again = run("jev", data)
        first = {v["id"]: v for v in results["runs"]["jev"]["verdicts"]}
        flips = [v["id"] for v in again["verdicts"] if v["verdict"] != first[v["id"]]["verdict"]]
        deltas = [abs(min(v["scores"][m] for m in reviewer.MATERIAL) - min(first[v["id"]]["scores"][m] for m in reviewer.MATERIAL))
                  for v in again["verdicts"] if v["scores"] and first[v["id"]]["scores"]]
        results["stability"] = {"verdict_flips": len(flips), "flipped": flips,
                                "mean_abs_delta_min_p": round(sum(deltas) / max(1, len(deltas)), 3),
                                "max_abs_delta_min_p": round(max(deltas, default=0.0), 3)}
        print("jev rerun:", results["stability"])
    # The full results quote every pair, the private ones included, so they
    # stay in the data folder. The report in the repository names a private
    # pair by its id and never quotes it: the repository is public.
    (paths.DATA / "bench_results.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    (ROOT / "bench" / "RESULTS.md").write_text(report(results))
    return 0


def report(results: dict) -> str:
    lines = [f"# Bullet reviewer benchmark ({results['date']})", "",
             f"{results['pairs']} labeled pairs: {results['pairs'] - results.get('private_pairs', 0)} synthetic "
             f"(bench/reviewer_pairs.py) and {results.get('private_pairs', 0)} from real resumes (kept out of the "
             f"repository), rubric `{results['rubric']}`, "
             f"reasoning model `{results['review_model']}`. Thresholds were calibrated on the even pairs; "
             "the table is the odd (held-out) pairs only. Per-resume figures assume "
             f"{PER_RESUME} changed bullets (an unchanged bullet costs nothing).", "",
             "| mode | false accept | false reject | ok sent to human | precision | recall | FAIL recall | "
             "escalated | $ per resume | s per resume |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for mode, r in results["runs"].items():
        m = r["metrics"]
        lines.append(f"| {mode} | {m['false_acceptance_rate']:.1%} | {m['false_rejection_rate']:.1%} | "
                     f"{m['human_review_rate_on_ok']:.1%} | {m['precision']:.1%} | {m['recall']:.1%} | "
                     f"{m['fail_recall']:.1%} | {m['escalated']} | ${r['per_resume']['usd']:.4f} | "
                     f"{r['per_resume']['seconds']} |")
    if "stability" in results:
        st = results["stability"]
        lines += ["", f"Jev run twice on all pairs: {st['verdict_flips']} verdicts changed; the lowest material "
                      f"P(accurate) moved {st['mean_abs_delta_min_p']} on average, {st['max_abs_delta_min_p']} at most."]
    if "calibration" in results:
        c = results["calibration"]
        lines += ["", f"Calibrated thresholds: pass_at {c['pass_at']}, fail_at {c['fail_at']}, "
                      f"escalate_at {c['escalate_at']}."]
    for mode, r in results["runs"].items():
        m = r["metrics"]
        lines += ["", f"## {mode}", "", "Recall by category: "
                  + ", ".join(f"{k} {v:.0%}" for k, v in m["recall_by_category"].items())]
        public = lambda i, t: "(a private pair from a real resume)" if i.startswith("r") else t
        if m["false_accepts"]:
            lines += ["", "False acceptances:"] + [f"- `{i}` {c}: {public(i, t)}" for i, c, t in m["false_accepts"]]
        if m["false_rejects"]:
            lines += ["", "False rejections:"] + [f"- `{i}`: {public(i, t)}" + ("" if i.startswith("r") else f" ({why})")
                                                    for i, why, t in m["false_rejects"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
