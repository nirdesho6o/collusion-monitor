"""Exploratory analysis and figures for Track 2.3.

Since we likely have zero or very few collusion-labelled runs, this analysis
is EXPLORATORY, not confirmatory. The main outputs are mechanism checks:

(a) Summary table: per-run raw Δ values
(b) Monitor agreement table: verdict counts by view across all runs
(c) Expert-minority diagnostic: mean predicted_yes_pct by view × own verdict
(d) Figure 1: predicted_yes_pct by view and own verdict (mechanism check)
(e) Figure 2: verdict distribution by view across runs
(f) If any run has positive Δ: SP vs majority vs oracle comparison

Reads monitor results JSON and extract_runs CSV. Outputs tables to stdout
and figures as PDF.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# (a) Summary table
# ---------------------------------------------------------------------------

def print_summary_table(extract_rows: List[Dict[str, Any]]):
    print("\n=== (a) SUMMARY TABLE: Per-run Δ values (EXPLORATORY) ===")
    print(f"{'run_id':<25} {'condition':<18} {'seed':>4} {'G':>8} {'Δ_raw':>8} {'secret':>6} {'dups':>4}")
    print("-" * 85)
    for r in extract_rows:
        dr = r.get("delta_raw", "")
        dr_str = f"{float(dr):>8.4f}" if dr and dr != "None" else "    N/A"
        print(f"{r['run_id']:<25} {r['condition']:<18} {r['seed']:>4} {float(r['G']):>8.4f} "
              f"{dr_str} {r['secret_msg_count']:>6} {r['duplicate_count']:>4}")
    print("\nNote: No τ set → no collusion labels assigned. All analysis is exploratory.")


# ---------------------------------------------------------------------------
# (b) Monitor agreement table
# ---------------------------------------------------------------------------

def print_monitor_agreement(monitor_data: List[Dict[str, Any]]):
    print("\n=== (b) MONITOR AGREEMENT: Verdict counts by view ===")
    by_view: Dict[str, Dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0, "invalid": 0, "total": 0})

    for s in monitor_data:
        view = s.get("view", "?")
        if s.get("valid") and s.get("parsed"):
            verdict = s["parsed"]["verdict"]
            by_view[view][verdict] += 1
        else:
            by_view[view]["invalid"] += 1
        by_view[view]["total"] += 1

    print(f"{'View':<6} {'Yes':>5} {'No':>5} {'Invalid':>7} {'Total':>6} {'Yes%':>6}")
    print("-" * 40)
    for view in sorted(by_view.keys()):
        d = by_view[view]
        valid = d["yes"] + d["no"]
        pct = f"{100*d['yes']/valid:.0f}" if valid > 0 else "N/A"
        print(f"{view:<6} {d['yes']:>5} {d['no']:>5} {d['invalid']:>7} {d['total']:>6} {pct:>5}%")


# ---------------------------------------------------------------------------
# (c) Expert-minority diagnostic
# ---------------------------------------------------------------------------

def print_expert_diagnostic(monitor_data: List[Dict[str, Any]]):
    print("\n=== (c) EXPERT-MINORITY DIAGNOSTIC: mean predicted_yes_pct by view × verdict ===")
    groups: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

    for s in monitor_data:
        if not (s.get("valid") and s.get("parsed")):
            continue
        view = s.get("view", "?")
        verdict = s["parsed"]["verdict"]
        pred = s["parsed"]["predicted_yes_pct"]
        groups[view][verdict].append(pred)

    print(f"{'View':<6} {'Verdict':<8} {'N':>4} {'Mean pred%':>10} {'Std':>6}")
    print("-" * 40)
    for view in sorted(groups.keys()):
        for verdict in ["yes", "no"]:
            vals = groups[view].get(verdict, [])
            if vals:
                mean = np.mean(vals)
                std = np.std(vals) if len(vals) > 1 else 0
                print(f"{view:<6} {verdict:<8} {len(vals):>4} {mean:>10.1f} {std:>6.1f}")
            else:
                print(f"{view:<6} {verdict:<8} {'0':>4} {'N/A':>10} {'N/A':>6}")

    print("\nKey question: Do V3 monitors who say 'yes' predict *lower* yes% than V1 monitors")
    print("who say 'yes'? That's the expert-minority signature (they know something others don't).")


# ---------------------------------------------------------------------------
# (d) Figure 1: predicted_yes_pct by view and own verdict
# ---------------------------------------------------------------------------

def figure_predicted_yes_pct(monitor_data: List[Dict[str, Any]], output_path: str):
    groups: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for s in monitor_data:
        if not (s.get("valid") and s.get("parsed")):
            continue
        view = s.get("view", "?")
        verdict = s["parsed"]["verdict"]
        groups[view][verdict].append(s["parsed"]["predicted_yes_pct"])

    views = sorted(groups.keys())
    if not views:
        print("No valid monitor data for Figure 1.", file=sys.stderr)
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(views))
    width = 0.35

    yes_means = []
    yes_stds = []
    no_means = []
    no_stds = []
    for v in views:
        y_vals = groups[v].get("yes", [])
        n_vals = groups[v].get("no", [])
        yes_means.append(np.mean(y_vals) if y_vals else 0)
        yes_stds.append(np.std(y_vals) if len(y_vals) > 1 else 0)
        no_means.append(np.mean(n_vals) if n_vals else 0)
        no_stds.append(np.std(n_vals) if len(n_vals) > 1 else 0)

    ax.bar(x - width/2, yes_means, width, yerr=yes_stds, label='Own verdict: yes',
           color='#d64541', alpha=0.8, capsize=3)
    ax.bar(x + width/2, no_means, width, yerr=no_stds, label='Own verdict: no',
           color='#3498db', alpha=0.8, capsize=3)

    ax.set_xlabel("Monitor view")
    ax.set_ylabel("Mean predicted yes % (of all monitors)")
    ax.set_title("Predicted yes % by view and own verdict\n(expert-minority mechanism check)")
    ax.set_xticks(x)
    ax.set_xticklabels(views)
    ax.legend()
    ax.set_ylim(0, 100)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved Figure 1 to {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# (e) Figure 2: verdict distribution by view across runs
# ---------------------------------------------------------------------------

def figure_verdict_distribution(monitor_data: List[Dict[str, Any]], output_path: str):
    by_view_run: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"yes": 0, "no": 0}))
    for s in monitor_data:
        if not (s.get("valid") and s.get("parsed")):
            continue
        view = s.get("view", "?")
        traj = s.get("trajectory", "?")
        by_view_run[view][traj][s["parsed"]["verdict"]] += 1

    views = sorted(by_view_run.keys())
    if not views:
        print("No valid monitor data for Figure 2.", file=sys.stderr)
        return

    # Compute yes-rate per (view, run)
    fig, ax = plt.subplots(figsize=(8, 5))
    positions = []
    data_by_view = {}
    for i, view in enumerate(views):
        yes_rates = []
        for traj, counts in by_view_run[view].items():
            total = counts["yes"] + counts["no"]
            if total > 0:
                yes_rates.append(counts["yes"] / total * 100)
        data_by_view[view] = yes_rates
        positions.append(i)

    bp = ax.boxplot([data_by_view.get(v, []) for v in views],
                    positions=positions, widths=0.5, patch_artist=True)
    colors = ['#3498db', '#f39c12', '#d64541']
    for patch, color in zip(bp['boxes'], colors[:len(views)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)

    # Overlay individual points
    for i, view in enumerate(views):
        rates = data_by_view.get(view, [])
        jitter = np.random.default_rng(42).uniform(-0.15, 0.15, len(rates))
        ax.scatter(np.full(len(rates), i) + jitter, rates, alpha=0.6, s=30, zorder=3, color='black')

    ax.set_xticks(positions)
    ax.set_xticklabels(views)
    ax.set_xlabel("Monitor view")
    ax.set_ylabel("Yes rate per run (%)")
    ax.set_title("Collusion verdict rate by view across runs\n(do informed monitors vote differently?)")
    ax.set_ylim(-5, 105)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved Figure 2 to {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# (f) SP vs majority vs oracle (if any positive Δ)
# ---------------------------------------------------------------------------

def print_sp_comparison(agg_rows: List[Dict[str, Any]], extract_rows: List[Dict[str, Any]]):
    positive_delta = [r for r in extract_rows
                      if r.get("delta_raw") and r["delta_raw"] != "None"
                      and float(r["delta_raw"]) > 0]
    if not positive_delta:
        print("\n=== (f) SP vs MAJORITY vs ORACLE: No runs with positive Δ_raw. Skipped. ===")
        return

    print("\n=== (f) SP vs MAJORITY vs ORACLE (runs with positive Δ_raw) ===")
    pos_ids = {r["run_id"] for r in positive_delta}
    relevant = [r for r in agg_rows if r.get("run_id") in pos_ids and r.get("pool") == "P_minority"]
    print(f"{'run_id':<25} {'aggregator':<20} {'predicted':>9}")
    print("-" * 60)
    for r in relevant:
        print(f"{r['run_id']:<25} {r['aggregator']:<20} {r.get('predicted_label', 'N/A'):>9}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-results", default=None, help="JSON from run_monitors.py")
    parser.add_argument("--extract-csv", required=True, help="CSV from extract_runs.py")
    parser.add_argument("--aggregate-csv", default=None, help="CSV from aggregate.py")
    parser.add_argument("--fig-dir", default="experiments/collusion/outputs/track23_tuning/figures",
                        help="Directory for figure PDFs")
    args = parser.parse_args()

    extract_rows = list(csv.DictReader(open(args.extract_csv)))
    print_summary_table(extract_rows)

    monitor_data = []
    if args.monitor_results and Path(args.monitor_results).exists():
        monitor_data = json.loads(Path(args.monitor_results).read_text())

    if monitor_data:
        print_monitor_agreement(monitor_data)
        print_expert_diagnostic(monitor_data)

        fig_dir = Path(args.fig_dir)
        fig_dir.mkdir(parents=True, exist_ok=True)
        figure_predicted_yes_pct(monitor_data, str(fig_dir / "fig1_predicted_yes_pct.pdf"))
        figure_verdict_distribution(monitor_data, str(fig_dir / "fig2_verdict_distribution.pdf"))
    else:
        print("\n=== Monitor results not available. Skipping (b), (c), (d), (e). ===")
        print("Run run_monitors.py first, then re-run this script with --monitor-results.")

    agg_rows = []
    if args.aggregate_csv and Path(args.aggregate_csv).exists():
        agg_rows = list(csv.DictReader(open(args.aggregate_csv)))

    if agg_rows:
        print_sp_comparison(agg_rows, extract_rows)
    else:
        print("\n=== (f) Aggregate results not available. Skipped. ===")

    print("\n=== ANALYSIS COMPLETE (exploratory — no confirmatory claims) ===")


if __name__ == "__main__":
    main()
