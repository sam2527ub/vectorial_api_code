#!/usr/bin/env python3
"""
Rebuild ``iteration_stats.jsonl``-style rows from ``post_traces.jsonl`` (same metrics as
``tier1_sgo_feedback_loop.py`` writes at each iteration end).

Optionally pulls ``refinement_call_count_end`` / shrink / qual counters from the latest
``evolution/state_history/*.json`` (or legacy ``evolution_state_history/*.json``) snapshot per
``last_completed_iteration``.

Usage (from repo root):

  python scripts/scripts_sgo/linkedin/aggregate_iteration_stats_from_traces.py \\
    --work-dir scripts/scripts_sgo/outputs/linkedin_tier1_sgo/default_run

  # also print the same banner lines the loop prints to stdout
  python .../aggregate_iteration_stats_from_traces.py --work-dir .../default_run --echo

  # overwrite iteration_stats.jsonl (backs up to .bak if --backup)
  python .../aggregate_iteration_stats_from_traces.py --work-dir .../default_run --in-place --backup
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _workdir_evolution_paths_read(work: Path) -> Tuple[Path, Path]:
    """Match ``tier1_sgo_feedback_loop.resolve_workdir_evolution_paths`` layout (read-only, no migration)."""
    nested_state = work / "evolution" / "evolution_state.json"
    nested_hist = work / "evolution" / "state_history"
    if nested_state.is_file():
        return nested_state, nested_hist
    return work / "evolution_state.json", work / "evolution_state_history"


def _load_jsd_by_iteration(traces_path: Path) -> Dict[int, List[float]]:
    by_iter: Dict[int, List[float]] = defaultdict(list)
    with traces_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            it = o.get("iteration")
            j = o.get("jsd")
            if it is None or j is None:
                continue
            by_iter[int(it)].append(float(j))
    return dict(by_iter)


def _best_counters_per_iteration(history_dir: Path) -> Dict[int, Dict[str, int]]:
    """
    For each last_completed_iteration value, keep the snapshot with the largest
    refinement_call_count (end-of-iteration-ish when counts increase monotonically).
    """
    best: Dict[int, Dict[str, int]] = {}
    if not history_dir.is_dir():
        return best
    for p in sorted(history_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        lit = d.get("last_completed_iteration")
        if lit is None:
            continue
        it = int(lit)
        rc = int(d.get("refinement_call_count") or 0)
        prev = best.get(it)
        if prev is None or rc >= prev["refinement_call_count"]:
            best[it] = {
                "refinement_call_count": rc,
                "shrink_call_count": int(d.get("shrink_call_count") or 0),
                "qual_refinement_call_count": int(d.get("qual_refinement_call_count") or 0),
                "qual_shrink_call_count": int(d.get("qual_shrink_call_count") or 0),
            }
    return best


def _merge_final_state(
    best: Dict[int, Dict[str, int]],
    state_path: Path,
) -> None:
    """If evolution_state.json has a higher iteration / counts, fold it in."""
    if not state_path.is_file():
        return
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    lit = d.get("last_completed_iteration")
    if lit is None:
        return
    it = int(lit)
    cand = {
        "refinement_call_count": int(d.get("refinement_call_count") or 0),
        "shrink_call_count": int(d.get("shrink_call_count") or 0),
        "qual_refinement_call_count": int(d.get("qual_refinement_call_count") or 0),
        "qual_shrink_call_count": int(d.get("qual_shrink_call_count") or 0),
    }
    prev = best.get(it)
    if prev is None or cand["refinement_call_count"] >= prev["refinement_call_count"]:
        best[it] = cand


def build_rows(
    *,
    jsd_by_iter: Dict[int, List[float]],
    counters_by_iter: Dict[int, Dict[str, int]],
    num_iterations_planned: int,
    jsd_threshold: float,
) -> List[Dict[str, Any]]:
    if not jsd_by_iter:
        return []
    max_seen = max(jsd_by_iter.keys())
    rows: List[Dict[str, Any]] = []
    for iteration in sorted(jsd_by_iter.keys()):
        js = jsd_by_iter[iteration]
        thr = float(jsd_threshold)
        n_posts = len(js)
        ctr = counters_by_iter.get(iteration) or {}
        row: Dict[str, Any] = {
            "iteration": iteration,
            "num_iterations_planned": int(num_iterations_planned),
            "n_posts_in_sweep": n_posts,
            "n_posts_with_jsd": len(js),
            "mean_jsd": round(statistics.mean(js), 6) if js else None,
            "median_jsd": round(statistics.median(js), 6) if js else None,
            "stdev_jsd": round(statistics.stdev(js), 6) if len(js) > 1 else None,
            "min_jsd": round(min(js), 6) if js else None,
            "max_jsd": round(max(js), 6) if js else None,
            "n_jsd_above_threshold": sum(1 for x in js if x > thr),
            "jsd_threshold": thr,
            "refinement_call_count_end": ctr.get("refinement_call_count"),
            "shrink_call_count_end": ctr.get("shrink_call_count"),
            "qual_refinement_call_count_end": ctr.get("qual_refinement_call_count"),
            "qual_shrink_call_count_end": ctr.get("qual_shrink_call_count"),
        }
        rows.append(row)

    # Warn if planned iterations > what we have traces for
    if max_seen < num_iterations_planned:
        pass  # caller may print
    return rows


def main() -> None:
    here = Path(__file__).resolve().parent
    default_work = (
        here.parent
        / "outputs"
        / "linkedin_tier1_sgo"
        / "default_run"
    )
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=default_work,
        help="Run directory containing post_traces.jsonl (and optional evolution/state_history/ or legacy evolution_state_history/)",
    )
    ap.add_argument("--jsd-threshold", type=float, default=0.3)
    ap.add_argument(
        "--num-iterations-planned",
        type=int,
        default=5,
        help="Written into each row as num_iterations_planned (informational)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Default: <work-dir>/iteration_stats_rebuilt.jsonl",
    )
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="Write to iteration_stats.jsonl inside work-dir instead of iteration_stats_rebuilt.jsonl",
    )
    ap.add_argument(
        "--backup",
        action="store_true",
        help="With --in-place, copy existing iteration_stats.jsonl to iteration_stats.jsonl.bak first",
    )
    ap.add_argument(
        "--echo",
        action="store_true",
        help="Print [ITER n] JSD | ... lines like tier1_sgo_feedback_loop.py",
    )
    args = ap.parse_args()

    work = args.work_dir.expanduser().resolve()
    traces_path = work / "post_traces.jsonl"
    if not traces_path.is_file():
        raise SystemExit(f"Missing traces file: {traces_path}")

    jsd_by_iter = _load_jsd_by_iteration(traces_path)
    state_path, hist = _workdir_evolution_paths_read(work)
    counters = _best_counters_per_iteration(hist)
    _merge_final_state(counters, state_path)

    rows = build_rows(
        jsd_by_iter=jsd_by_iter,
        counters_by_iter=counters,
        num_iterations_planned=args.num_iterations_planned,
        jsd_threshold=args.jsd_threshold,
    )
    if not rows:
        raise SystemExit("No (iteration, jsd) rows found in post_traces.jsonl")

    out_path = args.output
    if out_path is None:
        out_path = work / ("iteration_stats.jsonl" if args.in_place else "iteration_stats_rebuilt.jsonl")
    else:
        out_path = out_path.expanduser().resolve()

    if args.in_place and args.backup:
        ip = work / "iteration_stats.jsonl"
        if ip.is_file():
            shutil.copy2(ip, ip.with_suffix(".jsonl.bak"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as wf:
        for row in rows:
            wf.write(json.dumps(row, ensure_ascii=False) + "\n")

    max_iter = max(jsd_by_iter.keys())
    print(f"Wrote {len(rows)} line(s) → {out_path}")
    if max_iter < args.num_iterations_planned:
        print(
            f"Note: traces only contain iterations 1..{max_iter} "
            f"(num_iterations_planned={args.num_iterations_planned} is informational).",
            flush=True,
        )

    if args.echo:
        for row in rows:
            js = jsd_by_iter[int(row["iteration"])]
            thr = float(row["jsd_threshold"])
            if js:
                print(
                    f"[ITER {row['iteration']}] JSD | mean={statistics.mean(js):.6f} "
                    f"median={statistics.median(js):.6f} min={min(js):.6f} max={max(js):.6f} "
                    f"| n={len(js)} above_thr={row['n_jsd_above_threshold']} (thr={thr}) "
                    f"| refinement_end={row.get('refinement_call_count_end')}",
                    flush=True,
                )
            else:
                print(f"[ITER {row['iteration']}] JSD | (no rows)", flush=True)


if __name__ == "__main__":
    main()
