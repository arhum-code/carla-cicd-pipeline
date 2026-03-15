"""
CARLA-Specific Custom Static Analysis Rules
============================================
Detects antipatterns unique to CARLA Python API usage.
These go beyond what generic linters (flake8, pylint) catch.

Defect categories targeted:
  - API misuse (incorrect CARLA function calls / missing cleanup)
  - Resource management failures (actor leaks)
  - Synchronous mode misconfiguration
  - Sensor callback violations
"""

import ast
import json
import argparse
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


@dataclass
class RuleViolation:
    rule_id: str
    severity: str          # BLOCKER | CRITICAL | MAJOR | MINOR
    category: str          # maps to defect taxonomy
    file: str
    line: int
    message: str
    snippet: Optional[str] = None


@dataclass
class AnalysisResult:
    file: str
    violations: List[RuleViolation] = field(default_factory=list)
    analysis_time_ms: float = 0.0


class CARLARuleChecker(ast.NodeVisitor):
    """
    AST-based checker for CARLA-specific antipatterns.
    Each visit_* method implements one or more rules.
    """

    def __init__(self, filepath: str, source_lines: List[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.violations: List[RuleViolation] = []
        self._spawned_actors: List[int] = []    # track spawn call line numbers
        self._destroy_calls: int = 0
        self._in_sensor_callback: bool = False
        self._sync_mode_enabled: bool = False
        self._tick_in_callback: bool = False

    def _snippet(self, lineno: int) -> str:
        """Return the source line for a given line number."""
        if 0 < lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def _add(self, rule_id, severity, category, lineno, message):
        self.violations.append(RuleViolation(
            rule_id=rule_id,
            severity=severity,
            category=category,
            file=self.filepath,
            line=lineno,
            message=message,
            snippet=self._snippet(lineno),
        ))

    # ── Rule CRL-001: actor created without destroy() ─────────────────────
    def visit_Call(self, node: ast.Call):
        call_str = ast.unparse(node) if hasattr(ast, "unparse") else ""

        # Detect spawn_actor / try_spawn_actor calls
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr

            if method in ("spawn_actor", "try_spawn_actor"):
                self._spawned_actors.append(node.lineno)

            # Rule CRL-002: world.tick() inside sensor callback
            if method == "tick" and self._in_sensor_callback:
                self._add(
                    "CRL-002", "BLOCKER", "API Misuse",
                    node.lineno,
                    "world.tick() called inside a sensor callback. "
                    "This causes a deadlock in synchronous mode."
                )

            # Rule CRL-003: destroy() call found — good, track it
            if method == "destroy":
                self._destroy_calls += 1

            # Rule CRL-004: blueprint attribute accessed without get() validation
            if method == "set_attribute" and len(node.args) == 0:
                self._add(
                    "CRL-004", "MAJOR", "API Misuse",
                    node.lineno,
                    "blueprint.set_attribute() called with no arguments."
                )

            # Rule CRL-005: sensors attached without specifying transform
            if method in ("spawn_actor", "try_spawn_actor"):
                # Check if transform argument is None or missing
                if len(node.args) >= 2:
                    transform_arg = node.args[1]
                    if isinstance(transform_arg, ast.Constant) and transform_arg.value is None:
                        self._add(
                            "CRL-005", "MAJOR", "API Misuse",
                            node.lineno,
                            "Sensor spawned with None transform. "
                            "Specify an explicit carla.Transform() for attachment."
                        )

        self.generic_visit(node)

    # ── Rule CRL-006: synchronous mode enabled but never properly torn down
    def visit_Assign(self, node: ast.Assign):
        # Detect: settings.synchronous_mode = True
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            for target in node.targets:
                if isinstance(target, ast.Attribute) and "synchronous_mode" in target.attr:
                    self._sync_mode_enabled = True
        self.generic_visit(node)

    # ── Rule CRL-007: sensor callback detection for nested tick rule
    def visit_FunctionDef(self, node: ast.FunctionDef):
        # Heuristic: callbacks are often named on_*, listen lambdas, etc.
        if node.name.startswith("on_") or "callback" in node.name.lower():
            prev = self._in_sensor_callback
            self._in_sensor_callback = True
            self.generic_visit(node)
            self._in_sensor_callback = prev
        else:
            self.generic_visit(node)

    def finalize_file_checks(self, total_nodes: int):
        """
        Checks that require whole-file analysis (run after traversal).
        """
        # Rule CRL-001: spawned actors without matching destroy calls
        spawn_count = len(self._spawned_actors)
        if spawn_count > 0 and self._destroy_calls == 0:
            for lineno in self._spawned_actors:
                self._add(
                    "CRL-001", "CRITICAL", "Resource Management",
                    lineno,
                    f"Actor spawned at line {lineno} but no destroy() call found in file. "
                    "This causes memory leaks and orphaned actors in the CARLA server."
                )

        # Rule CRL-006: sync mode enabled but no cleanup detected
        if self._sync_mode_enabled and self._destroy_calls == 0:
            self._add(
                "CRL-006", "MAJOR", "API Misuse",
                1,
                "synchronous_mode = True detected but no cleanup/teardown pattern found. "
                "Ensure settings.synchronous_mode = False is called in a finally block."
            )


def analyze_file(filepath: str) -> AnalysisResult:
    """Run all CARLA custom rules on a single Python file."""
    start = time.time()
    result = AnalysisResult(file=filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=filepath)

        checker = CARLARuleChecker(filepath, source_lines)
        checker.visit(tree)
        checker.finalize_file_checks(len(source_lines))
        result.violations = checker.violations

    except SyntaxError as e:
        result.violations.append(RuleViolation(
            rule_id="CRL-000",
            severity="BLOCKER",
            category="Syntax Error",
            file=filepath,
            line=e.lineno or 0,
            message=f"SyntaxError: {e.msg}",
        ))
    except Exception as e:
        result.violations.append(RuleViolation(
            rule_id="CRL-ERR",
            severity="MINOR",
            category="Analysis Error",
            file=filepath,
            line=0,
            message=f"Analysis error: {str(e)}",
        ))

    result.analysis_time_ms = (time.time() - start) * 1000
    return result


def find_python_files(target_dir: str) -> List[str]:
    """Recursively find all Python files, excluding common non-source dirs."""
    exclude = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox"}
    files = []
    for root, dirs, filenames in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in exclude]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(os.path.join(root, fname))
    return files


def main():
    parser = argparse.ArgumentParser(description="CARLA custom static analysis rules")
    parser.add_argument("--target", required=True, help="Directory to analyze")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    args = parser.parse_args()

    python_files = find_python_files(args.target)
    print(f"Analyzing {len(python_files)} Python files for CARLA-specific issues...")

    all_results = []
    total_violations = 0

    for filepath in python_files:
        result = analyze_file(filepath)
        if result.violations:
            all_results.append(asdict(result))
            total_violations += len(result.violations)
            for v in result.violations:
                icon = "🔴" if v.severity in ("BLOCKER", "CRITICAL") else "🟡"
                print(f"  {icon} [{v.severity}] {v.rule_id} {filepath}:{v.line} — {v.message}")

    # Severity summary
    severity_counts = {"BLOCKER": 0, "CRITICAL": 0, "MAJOR": 0, "MINOR": 0}
    for r in all_results:
        for v in r["violations"]:
            sev = v["severity"]
            if sev in severity_counts:
                severity_counts[sev] += 1

    report = {
        "tool": "carla_custom_rules",
        "files_analyzed": len(python_files),
        "total_violations": total_violations,
        "severity_counts": severity_counts,
        "results": all_results,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nCARLA custom rules: {total_violations} violations across {len(python_files)} files")
    print(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
