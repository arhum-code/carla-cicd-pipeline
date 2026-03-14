"""
Failure Report Printer
========================
Formats Stage 1 summary into a readable developer feedback report
printed to stdout when the gate fails. This is what the developer sees
in the GitHub Actions log.
"""

import json
import argparse
import sys


SEVERITY_COLORS = {
    "BLOCKER":  "🔴",
    "CRITICAL": "🟠",
    "MAJOR":    "🟡",
    "MINOR":    "🔵",
    "INFO":     "⚪",
}

REMEDIATION_HINTS = {
    "CRL-001": "Add actor.destroy() in a finally block or use a cleanup list.",
    "CRL-002": "Move world.tick() outside the sensor callback. Use a queue instead.",
    "CRL-003": "Ensure destroy() is called for every spawned actor before script exit.",
    "CRL-004": "Provide both attribute name and value to set_attribute().",
    "CRL-005": "Pass an explicit carla.Transform() as the second argument to spawn_actor().",
    "CRL-006": "Wrap sync mode teardown in try/finally to ensure it always resets.",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    with open(args.summary) as f:
        summary = json.load(f)

    print("\n" + "═" * 60)
    print("  STAGE 1 FAILURE REPORT — Static Analysis")
    print("═" * 60)
    print(f"  Gate status : ❌ FAILED")
    print(f"  Reason      : {summary['gate_reason']}")
    print(f"  Total issues: {summary['total_violations']}")
    print()

    # Show only BLOCKER and CRITICAL
    blockers = [
        v for v in summary.get("violations", [])
        if v.get("severity") in ("BLOCKER", "CRITICAL")
    ]

    if blockers:
        print(f"  Issues requiring immediate fix ({len(blockers)} shown):")
        print("  " + "─" * 56)
        for v in blockers:
            icon = SEVERITY_COLORS.get(v["severity"], "⚪")
            print(f"\n  {icon} [{v['severity']}] {v.get('rule_id', '?')} — {v.get('tool', '?')}")
            print(f"     File    : {v['file']}:{v['line']}")
            print(f"     Issue   : {v['message']}")
            if v.get("snippet"):
                print(f"     Code    : {v['snippet']}")
            hint = REMEDIATION_HINTS.get(v.get("rule_id", ""), None)
            if hint:
                print(f"     Fix     : {hint}")

    print()
    print("  Fix the above issues and push again to re-run the pipeline.")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
