"""
Stage 1 Results Aggregator
===========================
Combines output from flake8, pylint, bandit, and CARLA custom rules
into a single summary JSON. Determines pass/fail gate outcome.
"""

import json
import argparse
import os
from pathlib import Path


SEVERITY_RANK = {"BLOCKER": 4, "CRITICAL": 3, "MAJOR": 2, "MINOR": 1, "INFO": 0}

# Gate: any finding at BLOCKER level blocks progression to Stage 2
GATE_THRESHOLD = "BLOCKER"


def load_json_safe(path: str) -> dict:
    """Load JSON file, return empty dict if missing or malformed."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_flake8(report: dict) -> list:
    """Normalize flake8 JSON output to common violation format."""
    violations = []
    for filepath, issues in report.items():
        for issue in issues:
            violations.append({
                "tool": "flake8",
                "rule_id": issue.get("code", "E000"),
                "severity": "MAJOR" if issue.get("code", "").startswith("E") else "MINOR",
                "category": "Logic Error",
                "file": filepath,
                "line": issue.get("line_number", 0),
                "message": issue.get("text", ""),
            })
    return violations


def parse_pylint(report) -> list:
    """Normalize pylint JSON output."""
    violations = []
    if not isinstance(report, list):
        return violations
    for issue in report:
        msg_type = issue.get("type", "")
        severity_map = {
            "error": "CRITICAL",
            "warning": "MAJOR",
            "convention": "MINOR",
            "refactor": "MINOR",
        }
        violations.append({
            "tool": "pylint",
            "rule_id": issue.get("message-id", "C0000"),
            "severity": severity_map.get(msg_type, "MINOR"),
            "category": "API Misuse" if msg_type == "error" else "Code Quality",
            "file": issue.get("path", ""),
            "line": issue.get("line", 0),
            "message": issue.get("message", ""),
        })
    return violations


def parse_bandit(report: dict) -> list:
    """Normalize bandit JSON output."""
    violations = []
    results = report.get("results", [])
    for issue in results:
        sev = issue.get("issue_severity", "LOW").upper()
        severity_map = {"HIGH": "CRITICAL", "MEDIUM": "MAJOR", "LOW": "MINOR"}
        violations.append({
            "tool": "bandit",
            "rule_id": issue.get("test_id", "B000"),
            "severity": severity_map.get(sev, "MINOR"),
            "category": "Security",
            "file": issue.get("filename", ""),
            "line": issue.get("line_number", 0),
            "message": issue.get("issue_text", ""),
        })
    return violations


def parse_carla_rules(report: dict) -> list:
    """Normalize CARLA custom rules output (already in common format)."""
    violations = []
    for result in report.get("results", []):
        for v in result.get("violations", []):
            violations.append({
                "tool": "carla_custom_rules",
                **v,
            })
    return violations


def compute_severity_counts(violations: list) -> dict:
    counts = {"BLOCKER": 0, "CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "INFO": 0}
    for v in violations:
        sev = v.get("severity", "INFO")
        if sev in counts:
            counts[sev] += 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    results_dir = Path(args.results_dir)

    # Load all tool reports
    flake8_data = load_json_safe(results_dir / "flake8_report.json")
    pylint_data = load_json_safe(results_dir / "pylint_report.json")
    bandit_data = load_json_safe(results_dir / "bandit_report.json")
    carla_data  = load_json_safe(results_dir / "carla_rules_report.json")

    # Normalize to common format
    all_violations = (
        parse_flake8(flake8_data) +
        parse_pylint(pylint_data) +
        parse_bandit(bandit_data) +
        parse_carla_rules(carla_data)
    )

    severity_counts = compute_severity_counts(all_violations)
    blocker_count   = severity_counts["BLOCKER"]
    critical_count  = severity_counts["CRITICAL"]

    # Gate decision
    gate_passed = blocker_count == 0
    gate_reason = (
        "No blocker-level defects detected."
        if gate_passed
        else f"{blocker_count} BLOCKER-level defect(s) must be resolved before simulation."
    )

    # Per-category breakdown
    category_counts: dict = {}
    for v in all_violations:
        cat = v.get("category", "Unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    summary = {
        "stage": 1,
        "gate_passed": gate_passed,
        "gate_reason": gate_reason,
        "total_violations": len(all_violations),
        "blocker_count": blocker_count,
        "critical_count": critical_count,
        "severity_counts": severity_counts,
        "category_counts": category_counts,
        "violations": all_violations,
        "tool_counts": {
            "flake8": len(parse_flake8(flake8_data)),
            "pylint": len(parse_pylint(pylint_data)),
            "bandit": len(parse_bandit(bandit_data)),
            "carla_custom_rules": sum(
                len(r.get("violations", [])) for r in carla_data.get("results", [])
            ),
        }
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(summary, f, indent=2)

    # Console summary
    status = "✅ PASSED" if gate_passed else "❌ FAILED"
    print(f"\nStage 1 Gate: {status}")
    print(f"  Total violations : {len(all_violations)}")
    print(f"  BLOCKER          : {blocker_count}")
    print(f"  CRITICAL         : {critical_count}")
    print(f"  Reason           : {gate_reason}")


if __name__ == "__main__":
    main()
