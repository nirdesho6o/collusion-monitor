"""Run Track 2.3 tuning runs one at a time under a daily Gemini request budget.

Each (seed, condition, replicate) is a separate `run_from_config` call with its own output root, so
same-seed control replicates never overwrite each other. Before each run the projected request total
(used today + estimate for one run) is checked against the cap; the estimate is the largest per-run
request count seen so far, or the worst case for the first run.

Usage:
  python -m experiments.collusion.track23.run_tuning \
    --config experiments/collusion/configs/collusion_jira_track23_tuning.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from experiments.collusion.run import run_from_config
from experiments.collusion.track23.claims import claim_takes, mentions_for_review, public_planning_messages
from experiments.common.rate_limit import get_pacer

# Default plan per seed: matched controls first, so a budget stop leaves complete control pairs.
DEFAULT_PLAN = [("control", 1), ("control", 2), ("emergent", 1), ("prompted", 1)]


def _worst_case_requests(cfg: Dict[str, Any]) -> int:
    sim = cfg.get("simulation") or {}
    agents = int((cfg.get("communication_network") or {}).get("num_agents", 6))
    rounds = int(sim.get("max_planning_rounds", 4))
    steps = int(sim.get("max_conversation_steps", 3))
    return agents * rounds * steps + agents * steps


def _load_manifest(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize_run(run_dir: Path) -> Dict[str, Any]:
    """Per-run report fields read from saved outputs."""
    rc = json.loads((run_dir / "run_config.json").read_text())
    events = json.loads((run_dir / "tool_events.json").read_text())
    turns = json.loads((run_dir / "agent_turns.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    secret_id = rc.get("secret_blackboard_id")
    posts = [e for e in events if e["tool_name"] == "post_message"]
    n_secret = sum(1 for e in posts if secret_id is not None and e["arguments"].get("blackboard_id") == secret_id)
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for turn in turns:
        for key in usage:
            usage[key] += int((turn.get("usage") or {}).get(key) or 0)
    non, coal = metrics.get("noncoalition_mean_regret"), metrics.get("coalition_mean_regret")
    messages = public_planning_messages(events, secret_id)
    return {
        "status": metrics.get("status"),
        "error_turns": sum(1 for t in turns if "[ERROR]" in str(t.get("response"))),
        "tokens": usage,
        "public_messages": len(posts) - n_secret,
        "secret_messages": n_secret,
        "regret_gap": (non - coal) if non is not None and coal is not None else None,
        "coalition_advantage_mean": metrics.get("coalition_advantage_mean"),
        "colluders": rc.get("colluders"),
        "turn_order": rc.get("agent_turn_order"),
        "claim_takes": claim_takes(events, secret_blackboard_id=secret_id, colluders=rc.get("colluders") or []),
        "mentions_for_review": [
            {"round": m["planning_round"], "agent": m["agent"], "message": m["message"]}
            for m in mentions_for_review(messages)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    parser.add_argument("--seeds", type=int, nargs="*", default=None, help="Default: experiment.seeds.")
    args = parser.parse_args()

    base = yaml.safe_load(Path(args.config).read_text())
    exp = base["experiment"]
    pacer = get_pacer(exp.get("request_pacing"))
    if pacer is None or pacer.daily_cap is None:
        raise SystemExit("experiment.request_pacing with daily_cap is required.")
    out_root = Path(exp["output_dir"])
    manifest_path = out_root / "manifest.jsonl"
    sweeps = {s["name"]: s for s in exp["sweeps"]}
    seeds = args.seeds or list(exp["seeds"])

    done = {(r["seed"], r["condition"], r["replicate"]) for r in _load_manifest(manifest_path) if r.get("valid")}
    observed = [r["requests"] for r in _load_manifest(manifest_path) if r.get("requests")]
    worst = _worst_case_requests(base)

    for seed in seeds:
        for condition, replicate in DEFAULT_PLAN:
            key = (seed, condition, replicate)
            if key in done:
                continue
            estimate = max(observed) if observed else worst
            used = pacer.used_today()
            if used + estimate > pacer.daily_cap:
                print(f"STOP before {key}: used {used} + estimate {estimate} > cap {pacer.daily_cap}")
                return

            cfg = copy.deepcopy(base)
            cfg["experiment"]["sweeps"] = [sweeps[condition]]
            cfg["experiment"]["seeds"] = [seed]
            run_out = out_root / f"{condition}_s{seed}_r{replicate}"
            cfg["experiment"]["output_dir"] = str(run_out)
            run_out.mkdir(parents=True, exist_ok=True)
            derived = run_out / "derived_config.yaml"
            derived.write_text(yaml.safe_dump(cfg, sort_keys=False))

            before = pacer.used_today()
            error: Optional[str] = None
            root: Optional[Path] = None
            try:
                root = asyncio.run(run_from_config(str(derived)))
            except Exception as exc:  # recorded in the manifest; the loop stops below
                error = f"{type(exc).__name__}: {exc}"
            requests = pacer.used_today() - before

            record: Dict[str, Any] = {
                "seed": seed,
                "condition": condition,
                "replicate": replicate,
                "requests": requests,
                "output_root": str(root) if root else None,
                "error": error,
                "budget_exhausted": pacer.exhausted,
            }
            run_dirs = sorted(root.glob("runs/*/*/*/")) if root else []
            if run_dirs and (run_dirs[0] / "metrics.json").exists() and (run_dirs[0] / "agent_turns.json").exists():
                record.update(summarize_run(run_dirs[0]))
            record["valid"] = bool(
                error is None
                and not pacer.exhausted
                and record.get("status") == "complete"
                and record.get("error_turns") == 0
            )
            if not record["valid"]:
                record["exclusion_reason"] = (
                    error or ("budget_exhausted" if pacer.exhausted else None)
                    or ("incomplete" if record.get("status") not in (None, "complete") else None)
                    or ("error_turns" if record.get("error_turns") else "missing_outputs")
                )
            with manifest_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
            print("RUN REPORT " + json.dumps(record))
            if record["valid"]:
                observed.append(requests)
            elif error is not None or pacer.exhausted:
                # Incomplete runs are excluded and logged; crashes and budget exhaustion stop the batch.
                print(f"STOP: run {key} invalid ({record['exclusion_reason']}).")
                return


if __name__ == "__main__":
    main()
