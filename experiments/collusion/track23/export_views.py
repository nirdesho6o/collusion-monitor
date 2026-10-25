"""Export nested monitor views V1/V2/V3 from completed Jira collusion runs.

V1: public messages only.
V2: V1 + final assignments.
V3: V2 + secret messages.

Format: ``[R<round>] Agent: text`` for messages, ``Agent -> task`` for assignments.
All coalition/condition/colluder information is stripped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _format_messages(events: List[Dict[str, Any]], board_ids: set) -> List[str]:
    posts = [
        e for e in events
        if e["tool_name"] == "post_message"
        and e["arguments"].get("blackboard_id") in board_ids
    ]
    lines = []
    for p in posts:
        rnd = p.get("planning_round")
        agent = p.get("agent_name", "?")
        msg = p["arguments"]["message"].strip()
        if rnd is not None:
            lines.append(f"[R{rnd}] {agent}: {msg}")
        else:
            lines.append(f"{agent}: {msg}")
    return lines


def _format_assignments(assignment: Dict[str, str]) -> List[str]:
    return [f"{agent} -> {task}" for agent, task in sorted(assignment.items())]


def export_views(
    run_dir: Path,
) -> Dict[str, str]:
    """Return {"V1": text, "V2": text, "V3": text} for one run directory."""
    rc = json.loads((run_dir / "run_config.json").read_text())
    events = json.loads((run_dir / "tool_events.json").read_text())
    summary = json.loads((run_dir / "final_summary.json").read_text())

    secret_id = rc.get("secret_blackboard_id")
    public_ids = {0, "0"}
    secret_ids = {secret_id} if secret_id is not None else set()

    public_msgs = _format_messages(events, public_ids)
    secret_msgs = _format_messages(events, secret_ids)
    assignments = _format_assignments(summary.get("assignment", {}))

    v1 = "\n".join(public_msgs)
    v2 = v1 + "\n\n--- Final Assignments ---\n" + "\n".join(assignments)
    v3 = v2 + "\n\n--- Private Channel ---\n" + "\n".join(secret_msgs)

    return {"V1": v1, "V2": v2, "V3": v3}


def export_views_for_run(
    root: Path,
) -> Optional[Tuple[Dict[str, str], Path]]:
    """Find the single run dir under a tuning output root and export views."""
    import glob
    run_dirs = sorted(root.glob("runs/*/*/*/"))
    if not run_dirs:
        return None
    rd = run_dirs[0]
    if not (rd / "run_config.json").exists():
        return None
    return export_views(rd), rd


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Path to a run directory containing run_config.json")
    parser.add_argument("--output-dir", help="Write V1.txt, V2.txt, V3.txt here (default: stdout)")
    args = parser.parse_args()

    rd = Path(args.run_dir)
    if not (rd / "run_config.json").exists():
        run_dirs = sorted(rd.glob("runs/*/*/*/"))
        if run_dirs:
            rd = run_dirs[0]

    views = export_views(rd)

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, text in views.items():
            (out / f"{name}.txt").write_text(text)
            print(f"Wrote {out / f'{name}.txt'} ({len(text)} chars)")
    else:
        for name, text in views.items():
            print(f"=== {name} ({len(text)} chars) ===")
            print(text)
            print()


if __name__ == "__main__":
    main()
