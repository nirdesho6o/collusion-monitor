"""Track 2.3 feasibility checks on Modal.

Usage (after `modal setup`):
  # (a) Deploy the monitor, run gate (c) check
  .venv/bin/modal run experiments/collusion/track23/modal_monitor.py

  # (b) Deploy the agent, get URL, then run Jira + Hospital
  .venv/bin/modal run experiments/collusion/track23/modal_agent.py
  # Copy the URL, then:
  .venv/bin/python experiments/collusion/track23/run_feasibility.py --base-url <URL>/v1

This script runs the agent feasibility tests (2b) once the Modal vLLM server is up.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any, Dict

import yaml


def _make_jira_config(base_url: str, condition: str) -> Dict[str, Any]:
    """Build a Jira config pointing at the remote vLLM agent."""
    cfg = {
        "simulation": {
            "max_iterations": 1,
            "max_planning_rounds": 4,
            "max_conversation_steps": 3,
            "seed": 1,
            "tags": [f"track23_agent_feasibility_{condition}"],
        },
        "environment": {
            "name": "JiraTicketEnvironment",
            "assignment_filling": False,
            "max_tasks": 8,
        },
        "communication_network": {
            "topology": "complete",
            "num_agents": 6,
            "consolidate_channels": True,
            "edge_prob": 0.6,
            "k": 2,
            "rewire_prob": 0.3,
            "m": 3,
        },
        "llm": {
            "provider": "vllm",
            "vllm": {
                "models": [{
                    "checkpoint": "NousResearch/Hermes-3-Llama-3.1-8B",
                    "served_model_name": "NousResearch/Hermes-3-Llama-3.1-8B",
                    "base_url": base_url,
                    "request_timeout": 120,
                }],
                "auto_start_server": False,
            },
            "params": {
                "max_tokens": 1500,
                "temperature": 0.7,
            },
        },
        "experiment": {
            "tag": "track23_agent_feasibility",
            "output_dir": f"experiments/collusion/outputs/track23_agent_feasibility/jira_{condition}",
            "max_concurrent_runs": 1,
            "seeds": [101],
            "collusion": {
                "colluder_selection": "random",
                "agent_order": "random",
            },
            "sweeps": [],
        },
    }

    if condition == "prompted":
        cfg["experiment"]["sweeps"] = [{
            "name": "prompted",
            "topologies": ["complete"],
            "num_agents": [6],
            "colluder_counts": [2],
            "secret_channel_enabled": [True],
            "prompt_variants": ["structured"],
        }]
    else:
        cfg["experiment"]["sweeps"] = [{
            "name": "control",
            "topologies": ["complete"],
            "num_agents": [6],
            "colluder_counts": [2],
            "secret_channel_enabled": [False],
            "prompt_variants": ["control"],
        }]

    return cfg


def _make_hospital_config(base_url: str, condition: str) -> Dict[str, Any]:
    """Build a Hospital config pointing at the remote vLLM agent."""
    cfg = {
        "simulation": {
            "max_iterations": 1,
            "max_planning_rounds": 2,
            "max_conversation_steps": 3,
            "seed": 1,
            "tags": [f"track23_agent_feasibility_hospital_{condition}"],
        },
        "environment": {
            "import_path": "envs.dcops.hospital.hospital_env:HospitalEnvironment",
            "name": "HospitalEnvironment",
            "num_patients": 8,
            "num_hospitals": 2,
        },
        "communication_network": {
            "topology": "complete",
            "num_agents": 9,
            "consolidate_channels": True,
            "edge_prob": 0.6,
            "k": 2,
            "rewire_prob": 0.3,
            "m": 3,
        },
        "llm": {
            "provider": "vllm",
            "vllm": {
                "models": [{
                    "checkpoint": "NousResearch/Hermes-3-Llama-3.1-8B",
                    "served_model_name": "NousResearch/Hermes-3-Llama-3.1-8B",
                    "base_url": base_url,
                    "request_timeout": 120,
                }],
                "auto_start_server": False,
            },
            "params": {
                "max_tokens": 1500,
                "temperature": 0.7,
            },
        },
        "experiment": {
            "tag": "track23_agent_feasibility_hospital",
            "output_dir": f"experiments/collusion/outputs/track23_agent_feasibility/hospital_{condition}",
            "max_concurrent_runs": 1,
            "seeds": [101],
            "collusion": {
                "colluder_selection": "random",
                "agent_order": "random",
            },
            "sweeps": [],
        },
    }

    if condition == "prompted":
        cfg["experiment"]["sweeps"] = [{
            "name": "prompted",
            "topologies": ["complete"],
            "num_agents": [9],
            "colluder_counts": [4],
            "secret_channel_enabled": [True],
            "prompt_variants": ["structured"],
        }]
    else:
        cfg["experiment"]["sweeps"] = [{
            "name": "control",
            "topologies": ["complete"],
            "num_agents": [9],
            "colluder_counts": [4],
            "secret_channel_enabled": [False],
            "prompt_variants": ["control"],
        }]

    return cfg


def run_one(cfg: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Run a single experiment and return a summary."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

    from experiments.collusion.run import run_from_config

    out_dir = Path(cfg["experiment"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "derived_config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    t0 = time.time()
    error = None
    root = None
    try:
        root = asyncio.run(run_from_config(str(config_path)))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    wall_time = time.time() - t0

    result = {
        "label": label,
        "wall_time_s": round(wall_time, 1),
        "error": error,
        "output_root": str(root) if root else None,
    }

    if root:
        import glob
        run_dirs = sorted(glob.glob(f"{root}/runs/*/*/*/"))
        if run_dirs:
            rd = Path(run_dirs[0])
            if (rd / "metrics.json").exists():
                metrics = json.loads((rd / "metrics.json").read_text())
                events = json.loads((rd / "tool_events.json").read_text())
                turns = json.loads((rd / "agent_turns.json").read_text())
                rc = json.loads((rd / "run_config.json").read_text())

                secret_id = rc.get("secret_blackboard_id")
                posts = [e for e in events if e["tool_name"] == "post_message"]
                n_secret = sum(1 for e in posts if secret_id and e["arguments"].get("blackboard_id") == secret_id)
                error_turns = sum(1 for t in turns if "[ERROR]" in str(t.get("response")))

                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                for turn in turns:
                    for key in usage:
                        usage[key] += int((turn.get("usage") or {}).get(key) or 0)

                ncr = metrics.get("noncoalition_mean_regret", 0)
                cr = metrics.get("coalition_mean_regret", 0)

                result.update({
                    "status": metrics.get("status"),
                    "error_turns": error_turns,
                    "tokens": usage,
                    "secret_messages": n_secret,
                    "regret_gap": round(ncr - cr, 4) if ncr is not None and cr is not None else None,
                    "colluders": rc.get("colluders"),
                })

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Modal vLLM base URL (ending in /v1)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    results = []

    # 1. Jira Prompted seed 101
    print("=== Jira Prompted s101 ===")
    cfg = _make_jira_config(base_url, "prompted")
    r = run_one(cfg, "jira_prompted_s101")
    results.append(r)
    print(json.dumps(r, indent=2))

    # 2. Jira Control seed 101
    print("\n=== Jira Control s101 ===")
    cfg = _make_jira_config(base_url, "control")
    r = run_one(cfg, "jira_control_s101")
    results.append(r)
    print(json.dumps(r, indent=2))

    # 3. Hospital Prompted seed 101
    print("\n=== Hospital Prompted s101 ===")
    cfg = _make_hospital_config(base_url, "prompted")
    r = run_one(cfg, "hospital_prompted_s101")
    results.append(r)
    print(json.dumps(r, indent=2))

    # 4. Hospital Control seed 101
    print("\n=== Hospital Control s101 ===")
    cfg = _make_hospital_config(base_url, "control")
    r = run_one(cfg, "hospital_control_s101")
    results.append(r)
    print(json.dumps(r, indent=2))

    # Save results
    out = Path("experiments/collusion/outputs/track23_agent_feasibility")
    out.mkdir(parents=True, exist_ok=True)
    (out / "feasibility_results.json").write_text(json.dumps(results, indent=2))

    print("\n=== SUMMARY ===")
    for r in results:
        status = r.get("status", r.get("error", "?"))
        print(f"  {r['label']}: {status}, wall={r['wall_time_s']}s, "
              f"secret={r.get('secret_messages','?')}, G={r.get('regret_gap','?')}, "
              f"err_turns={r.get('error_turns','?')}")


if __name__ == "__main__":
    main()
