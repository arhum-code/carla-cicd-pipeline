"""
Failure Report Printer
========================
Formats Stage 1 summary into a readable developer feedback report.
Filters out pre-existing baseline violations so only NEW issues fail the gate.
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

def load_baseline(path):
    """Load baseline violations to exclude from gate check."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def is_baseline_violation(v, baseline):
    """Check if a violation matches a known baseline entry."""
    for b in baseline:
        b_file = b['file'].replace('test_projects/scenario_runner/', '') \
                          .replace('test_projects/leaderboard/', '')
        v_file = v['file'].replace('test_projects/scenario_runner/', '') \
                          .replace('test_projects/leaderboard/', '')
        if (b['rule_id'] == v['rule_id'] and
            b_file in v_file and
            abs(b['line'] - v['line']) <= 5):
            return True
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--baseline", default=None,
                        help="Path to baseline JSON to exclude pre-existing violations")
    args = parser.parse_args()

    with open(args.summary) as f:
        summary = json.load(f)

    baseline = load_baseline(args.baseline) if args.baseline else []

    # Collect all violations
    violations = []
    for file_result in summary.get("results", []):
        for v in file_result.get("violations", []):
            violations.append(v)

    # Split into new vs baseline
    new_violations = [v for v in violations if not is_baseline_violation(v, baseline)]
    baseline_violations = [v for v in violations if is_baseline_violation(v, baseline)]

    blockers = [v for v in new_violations if v.get("severity") in ("BLOCKER", "CRITICAL")]

    print(f"\n{'═' * 60}")
    print(f"  STAGE 1 GATE REPORT — Static Analysis")
    print(f"{'═' * 60}")
    print(f"  Total findings      : {len(violations)}")
    print(f"  Baseline (excluded) : {len(baseline_violations)}")
    print(f"  New violations      : {len(new_violations)}")

    if baseline_violations:
        print(f"\n  Baseline violations (pre-existing, excluded from gate):")
        for v in baseline_violations:
            icon = SEVERITY_COLORS.get(v["severity"], "⚪")
            print(f"    {icon} [{v['severity']}] {v.get('rule_id')} — {v['file']}:{v['line']}")

    if not blockers:
        print(f"\n✅ Gate PASSED — no new BLOCKER or CRITICAL violations.\n")
        sys.exit(0)

    print(f"\n  Gate status : ❌ FAILED")
    print(f"  Reason      : {len(blockers)} new BLOCKER/CRITICAL violation(s) found")
    print(f"\n  New issues requiring immediate fix:")
    print(f"  {'─' * 56}")

    for v in blockers:
        icon = SEVERITY_COLORS.get(v["severity"], "⚪")
        print(f"\n  {icon} [{v['severity']}] {v.get('rule_id', '?')} — carla_custom_rules")
        print(f"     File    : {v['file']}:{v['line']}")
        print(f"     Issue   : {v['message']}")
        if v.get("snippet"):
            print(f"     Code    : {v['snippet']}")
        hint = REMEDIATION_HINTS.get(v.get("rule_id", ""), None)
        if hint:
            print(f"     Fix     : {hint}")

    print(f"\n  Fix the above issues and push again to re-run the pipeline.")
    print(f"{'═' * 60}\n")
    sys.exit(1)

if __name__ == "__main__":
    main()
