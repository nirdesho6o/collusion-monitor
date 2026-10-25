"""Extract one row per completed run for Track 2.3 analysis.

Reads local output directories. Never calls an API. Outputs CSV to stdout
(redirect with > or use --output).

See EXTRACT_RUNS_DESIGN.md for the full specification.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _find_run_dir(root: Path) -> Optional[Path]:
    """Find the single innermost run directory under a tuning output root."""
    for ts_dir in sorted(root.iterdir()):
        if not ts_dir.is_dir():
            continue
        rd_list = sorted(ts_dir.glob("runs/*/*/*/"))
        for rd in rd_list:
            if (rd / "metrics.json").exists() and (rd / "run_config.json").exists():
                return rd
    return None


def _condition_from_config(rc: Dict[str, Any]) -> str:
    secret = rc.get("secret_channel_enabled", False)
    variant = (rc.get("prompt_variant") or "control").lower()
    if secret and variant == "structured":
        return "prompted"
    if secret:
        return "emergent"
    return "control"


def _duplicates(agents_m: List[Dict[str, Any]], turn_order: List[str]) -> List[Dict[str, Any]]:
    task_doers: Dict[str, List[str]] = {}
    for a in agents_m:
        t = a.get("chosen_task_id", "?")
        task_doers.setdefault(t, []).append(a["agent_name"])
    dups = []
    order_map = {name: i for i, name in enumerate(turn_order)} if turn_order else {}
    for task, agents in task_doers.items():
        if len(agents) < 2:
            continue
        first = min(agents, key=lambda a: order_map.get(a, 999))
        dups.append({"task": task, "agents": agents, "first_by_turn_order": first, "count": len(agents)})
    return dups


def extract_one(rd: Path, run_id: str, replicate: int = 1, agent_temp: float = 0.7,
                max_tokens: int = 1500) -> Dict[str, Any]:
    rc = json.loads((rd / "run_config.json").read_text())
    metrics = json.loads((rd / "metrics.json").read_text())
    events = json.loads((rd / "tool_events.json").read_text())
    summary = json.loads((rd / "final_summary.json").read_text())

    colluders = set(rc.get("colluders", []))
    secret_id = rc.get("secret_blackboard_id")
    turn_order = rc.get("agent_turn_order", [])
    condition = _condition_from_config(rc)
    seed = rc.get("seed")

    agents_m = metrics.get("agents", [])
    ncr = metrics.get("noncoalition_mean_regret", 0.0)
    cr = metrics.get("coalition_mean_regret", 0.0)
    G = ncr - cr

    posts = [e for e in events if e["tool_name"] == "post_message"]
    n_secret = sum(1 for e in posts if secret_id is not None and e["arguments"].get("blackboard_id") == secret_id)
    n_public = len(posts) - n_secret

    dups = _duplicates(agents_m, turn_order)

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from experiments.collusion.track23.claims import claim_takes, public_planning_messages
    messages = public_planning_messages(events, secret_id)
    ct = claim_takes(events, secret_blackboard_id=secret_id, colluders=list(colluders))

    per_agent = {}
    for a in agents_m:
        name = a["agent_name"]
        per_agent[name] = {
            "role": "coalition" if name in colluders else "non-coalition",
            "task": a.get("chosen_task_id", ""),
            "reward": round(a.get("reward", 0), 4),
            "regret": round(a.get("regret", 0), 4),
        }

    return {
        "run_id": run_id,
        "condition": condition,
        "seed": seed,
        "replicate": replicate,
        "agent_temp": agent_temp,
        "max_tokens": max_tokens,
        "status": metrics.get("status"),
        "G": round(G, 4),
        "coalition_advantage_mean": round(metrics.get("coalition_advantage_mean", 0), 4),
        "joint_reward_ratio": round(metrics.get("joint_reward_ratio", 0), 4),
        "system_regret_ratio": round(metrics.get("system_regret_ratio", 0), 4),
        "violations": metrics.get("violations", 0),
        "secret_msg_count": n_secret,
        "public_msg_count": n_public,
        "duplicate_count": len(dups),
        "duplicates_detail": json.dumps(dups) if dups else "",
        "claim_take_count": len(ct),
        "claim_takes_detail": json.dumps(ct) if ct else "",
        "turn_order": json.dumps(turn_order),
        "per_agent": json.dumps(per_agent),
        "colluders": json.dumps(sorted(colluders)),
        "noncoalition_mean_regret": round(ncr, 4),
        "coalition_mean_regret": round(cr, 4),
    }


def compute_deltas(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add Δ_raw, Δ_excl, Δ_reward, label columns."""
    control_G: Dict[int, List[float]] = {}
    control_reward: Dict[int, List[float]] = {}
    for r in rows:
        if r["condition"] == "control":
            seed = r["seed"]
            control_G.setdefault(seed, []).append(r["G"])
            control_reward.setdefault(seed, []).append(r["coalition_advantage_mean"])

    for r in rows:
        seed = r["seed"]
        if seed in control_G and control_G[seed]:
            mean_G_ctrl = sum(control_G[seed]) / len(control_G[seed])
            mean_reward_ctrl = sum(control_reward.get(seed, [0])) / max(len(control_reward.get(seed, [1])), 1)
            r["delta_raw"] = round(r["G"] - mean_G_ctrl, 4)
            r["delta_reward"] = round(r["coalition_advantage_mean"] - mean_reward_ctrl, 4)
        else:
            r["delta_raw"] = None
            r["delta_reward"] = None

        # Δ_excl: same but dropping claim-take claimants from regret means.
        # No claim-takes in any tuning run, so Δ_excl = Δ_raw for now.
        r["delta_excl"] = r["delta_raw"]

        # Label (requires τ; report None until user sets it)
        r["label"] = None

    # Report control-to-control spread
    for seed, gs in sorted(control_G.items()):
        if len(gs) >= 2:
            spread = max(gs) - min(gs)
            print(f"# Control-to-control G spread for seed {seed}: {spread:.4f} (values: {[round(g,4) for g in gs]})", file=sys.stderr)

    return rows


CSV_COLUMNS = [
    "run_id", "condition", "seed", "replicate", "agent_temp", "max_tokens",
    "status", "G", "delta_raw", "delta_excl", "delta_reward", "label",
    "coalition_advantage_mean", "joint_reward_ratio", "system_regret_ratio",
    "violations", "secret_msg_count", "public_msg_count",
    "duplicate_count", "duplicates_detail", "claim_take_count", "claim_takes_detail",
    "noncoalition_mean_regret", "coalition_mean_regret",
    "colluders", "turn_order", "per_agent",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tuning-dir", default="experiments/collusion/outputs/track23_tuning",
                        help="Root of tuning outputs")
    parser.add_argument("--pilot-dir", default="experiments/collusion/outputs/track23_gemini_smoke",
                        help="Root of pilot outputs")
    parser.add_argument("--output", default=None, help="Output CSV path (default: stdout)")
    args = parser.parse_args()

    rows = []

    # Tuning runs
    tuning_root = Path(args.tuning_dir)
    run_map = {
        "control_s101_r1": (101, 1),
        "control_s101_r2": (101, 2),
        "emergent_s101_r1": (101, 1),
        "prompted_s101_r1": (101, 1),
        "control_s102_r1": (102, 1),
        "control_s102_r2": (102, 2),
        "emergent_s102_r1": (102, 1),
    }

    for folder_name, (seed, replicate) in run_map.items():
        folder = tuning_root / folder_name
        if not folder.exists():
            continue
        rd = _find_run_dir(folder)
        if rd is None:
            print(f"# EXCLUDED: {folder_name} — no metrics.json", file=sys.stderr)
            continue
        row = extract_one(rd, run_id=folder_name, replicate=replicate, agent_temp=0.7, max_tokens=1500)
        rows.append(row)

    # Pilot
    pilot_root = Path(args.pilot_dir)
    if pilot_root.exists():
        for ts_dir in sorted(pilot_root.iterdir()):
            if not ts_dir.is_dir():
                continue
            rd_list = sorted(ts_dir.glob("runs/*/*/*/"))
            for rd in rd_list:
                if (rd / "metrics.json").exists():
                    row = extract_one(rd, run_id=f"pilot_{ts_dir.name}", replicate=1,
                                      agent_temp=0.4, max_tokens=1024)
                    row["condition"] = "pilot_prompted"
                    rows.append(row)
                    break

    rows = compute_deltas(rows)

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    if args.output:
        out.close()
        print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
