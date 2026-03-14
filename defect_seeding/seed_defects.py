"""
Defect Seeding Automation Script
==================================
Automates defect seeding across CARLA projects using three sources:
  1. Real Bug Commits  — mines git log, reverses bug-fix patches
  2. Mutation Operators — applies 5 predefined code transformations
  3. Hand-Crafted Scenario Errors — malforms OpenSCENARIO .xosc files

Output: ground_truth.json recording every seeded defect with:
  - file, line, category, severity, symptom, source, detected_by_stage1

Usage:
  python3 seed_defects.py --project-dir test_projects/scenario_runner
                          --output defect_seeding/ground_truth.json
                          --project-name scenario_runner
                          --max-defects 12
                          --sources all
"""

import os
import re
import ast
import json
import shutil
import argparse
import subprocess
import random
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# ── Defect taxonomy categories ────────────────────────────────────────────────
CATEGORIES = [
    "API Misuse",
    "Resource Management",
    "Logic Error",
    "Scenario Specification",
    "Integration Error",
    "Robustness Failure",
]

SEVERITIES = ["BLOCKER", "CRITICAL", "MAJOR", "MINOR"]


@dataclass
class SeededDefect:
    """Ground truth record for one seeded defect."""
    defect_id:          str
    project:            str
    source:             str          # "real_bug" | "mutation" | "handcrafted"
    category:           str
    severity:           str
    file_path:          str
    line_number:        int
    description:        str
    symptom:            str          # observable failure during simulation
    original_code:      str          # what the line looked like before seeding
    seeded_code:        str          # what it looks like after seeding
    commit_hash:        Optional[str] = None   # for real_bug source
    detected_by_stage1: bool = False
    detected_by_stage2: bool = False
    detected_by_stage3: bool = False
    seeded_at:          str = field(default_factory=lambda: datetime.now().isoformat())


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1 — Real Bug Commits
# ══════════════════════════════════════════════════════════════════════════════

BUG_KEYWORDS = ["fix", "bug", "crash", "error", "issue", "broken", "wrong",
                 "fail", "incorrect", "typo", "patch", "hotfix", "resolve"]

# Map common CARLA bug patterns to taxonomy categories
BUG_PATTERN_CATEGORY = {
    r"destroy":              ("Resource Management", "CRITICAL"),
    r"world\.tick":          ("API Misuse",          "BLOCKER"),
    r"sensor":               ("API Misuse",          "CRITICAL"),
    r"transform":            ("API Misuse",          "MAJOR"),
    r"set_attribute":        ("API Misuse",          "MAJOR"),
    r"synchronous":          ("Resource Management", "CRITICAL"),
    r"blueprint":            ("API Misuse",          "MAJOR"),
    r"spawn":                ("API Misuse",          "CRITICAL"),
    r"callback":             ("Integration Error",   "MAJOR"),
    r"throttle|brake|steer": ("Logic Error",         "CRITICAL"),
    r"lane|waypoint":        ("Logic Error",         "MAJOR"),
    r"lidar|camera|radar":   ("Robustness Failure",  "MAJOR"),
}


def get_bug_commits(project_dir: str, max_commits: int = 200) -> List[dict]:
    """
    Mine git log for commits whose messages contain bug-fix keywords.
    Returns list of {hash, message, files_changed}.
    """
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}",
             "--oneline", "--no-merges"],
            cwd=project_dir,
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"  git log failed: {result.stderr.strip()}")
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("  git not available or project has no history")
        return []

    bug_commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        commit_hash, message = parts[0], parts[1]
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in BUG_KEYWORDS):
            bug_commits.append({
                "hash":    commit_hash,
                "message": message,
            })

    print(f"  Found {len(bug_commits)} bug-fix commits in git log")
    return bug_commits


def get_commit_diff(project_dir: str, commit_hash: str) -> Optional[str]:
    """Get the unified diff for a single commit."""
    try:
        result = subprocess.run(
            ["git", "show", "--unified=3", commit_hash],
            cwd=project_dir,
            capture_output=True, text=True, timeout=15
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def categorize_diff(diff: str) -> tuple:
    """
    Classify a diff into a taxonomy category based on patterns.
    Returns (category, severity).
    """
    diff_lower = diff.lower()
    for pattern, (cat, sev) in BUG_PATTERN_CATEGORY.items():
        if re.search(pattern, diff_lower):
            return cat, sev
    return "Logic Error", "MAJOR"


def parse_diff_for_revert(diff: str) -> List[dict]:
    """
    Parse a unified diff to find lines that were added in the fix
    (lines starting with +, excluding +++).
    These are the lines we need to REMOVE to reintroduce the bug.
    Also finds lines that were removed (starting with -) — the original buggy code.
    Returns list of {file, removed_line (original bug), added_line (fix), line_number}.
    """
    hunks = []
    current_file = None
    current_line = 0

    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
        elif line.startswith("@@ "):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r"\+(\d+)", line)
            if m:
                current_line = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            # This line was ADDED by the fix — removing it reintroduces the bug
            if current_file and current_file.endswith(".py"):
                hunks.append({
                    "file":         current_file,
                    "line_number":  current_line,
                    "fix_line":     line[1:],   # line as fixed
                    "bug_line":     "",          # will be empty (line was added)
                })
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            # This line was REMOVED by the fix — it was the original buggy code
            if hunks and hunks[-1]["file"] == current_file:
                hunks[-1]["bug_line"] = line[1:]
            current_line += 1 if not line.startswith("-") else 0
        elif not line.startswith("-"):
            current_line += 1

    return hunks


def apply_real_bug_revert(
    project_dir: str,
    commit: dict,
    defect_counter: int,
    project_name: str
) -> Optional[SeededDefect]:
    """
    Attempt to reintroduce the original bug from a fix commit.
    Finds the first Python file changed, locates a meaningful line,
    and reverts it.
    """
    diff = get_commit_diff(project_dir, commit["hash"])
    if not diff:
        return None

    category, severity = categorize_diff(diff)
    hunks = parse_diff_for_revert(diff)

    # Filter to hunks with actual content to revert
    viable = [h for h in hunks if h["fix_line"].strip()
              and len(h["fix_line"].strip()) > 5]
    if not viable:
        return None

    hunk = viable[0]
    rel_path = hunk["file"]
    abs_path = os.path.join(project_dir, rel_path)

    if not os.path.exists(abs_path):
        return None

    with open(abs_path, "r", errors="ignore") as f:
        lines = f.readlines()

    line_idx = hunk["line_number"] - 1
    if line_idx < 0 or line_idx >= len(lines):
        return None

    original_line = lines[line_idx]

    # The reversion: remove the fix line (comment it out with a marker)
    seeded_line = f"# SEEDED_DEFECT: reverted fix from {commit['hash'][:8]}\n"

    lines[line_idx] = seeded_line
    with open(abs_path, "w") as f:
        f.writelines(lines)

    defect_id = f"D{defect_counter:03d}"
    return SeededDefect(
        defect_id=defect_id,
        project=project_name,
        source="real_bug",
        category=category,
        severity=severity,
        file_path=rel_path,
        line_number=hunk["line_number"],
        description=f"Reverted fix: {commit['message'][:80]}",
        symptom=f"Defect reintroduced from commit {commit['hash'][:8]}",
        original_code=original_line.rstrip(),
        seeded_code=seeded_line.rstrip(),
        commit_hash=commit["hash"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2 — Mutation Operators
# ══════════════════════════════════════════════════════════════════════════════

def find_python_files(project_dir: str) -> List[str]:
    """Find all Python files in the project."""
    py_files = []
    for root, dirs, files in os.walk(project_dir):
        # Skip hidden dirs and common non-source dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and d not in ("__pycache__", ".git", "venv", "env")]
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files


def mutation_remove_sensor_validation(
    py_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    MUT-001: Remove sensor data validation check.
    Finds: if data is None / if sensor_data is None / assert data
    Seeds: comment out the check
    """
    patterns = [
        r"if\s+(data|sensor_data|image|point_cloud|measurement)\s+is\s+None",
        r"assert\s+(data|sensor_data|image)\s+is\s+not\s+None",
        r"if\s+not\s+(data|sensor_data|image)",
    ]
    for f in py_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            for pat in patterns:
                if re.search(pat, line) and not line.strip().startswith("#"):
                    original = line.rstrip()
                    lines[i] = f"# SEEDED MUT-001 (sensor validation removed): {line.rstrip()}\n"
                    with open(f, "w") as fh:
                        fh.writelines(lines)
                    rel = os.path.relpath(f, project_dir)
                    return SeededDefect(
                        defect_id=f"D{defect_counter:03d}",
                        project=project_name,
                        source="mutation",
                        category="Robustness Failure",
                        severity="CRITICAL",
                        file_path=rel,
                        line_number=i + 1,
                        description="MUT-001: Sensor data validation check removed",
                        symptom="NullPointerError or silent None propagation during sensor callback",
                        original_code=original,
                        seeded_code=lines[i].rstrip(),
                    )
    return None


def mutation_invert_control_logic(
    py_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    MUT-002: Invert conditional logic in agent controller.
    Finds: if speed > threshold / if distance < min_dist
    Seeds: flips > to < or < to >
    """
    patterns = [
        (r"(vehicle_speed|speed|current_speed)\s*>\s*(\d+)", ">", "<"),
        (r"(vehicle_speed|speed|current_speed)\s*<\s*(\d+)", "<", ">"),
        (r"(distance|following_distance)\s*<\s*(\d+\.?\d*)",  "<", ">"),
    ]
    for f in py_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if "def " in line or line.strip().startswith("#"):
                continue
            for pat, old_op, new_op in patterns:
                if re.search(pat, line):
                    original = line.rstrip()
                    # Replace first occurrence of the operator in context
                    seeded = re.sub(pat,
                        lambda m: m.group(0).replace(old_op, new_op, 1), line, count=1)
                    if seeded != line:
                        lines[i] = seeded
                        with open(f, "w") as fh:
                            fh.writelines(lines)
                        rel = os.path.relpath(f, project_dir)
                        return SeededDefect(
                            defect_id=f"D{defect_counter:03d}",
                            project=project_name,
                            source="mutation",
                            category="Logic Error",
                            severity="CRITICAL",
                            file_path=rel,
                            line_number=i + 1,
                            description=f"MUT-002: Control logic inverted ({old_op} → {new_op})",
                            symptom="Agent accelerates when it should brake or vice versa",
                            original_code=original,
                            seeded_code=seeded.rstrip(),
                        )
    return None


def mutation_off_by_one(
    py_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    MUT-003: Introduce off-by-one error in array/list indexing.
    Finds: waypoints[i] / waypoints[0] / path[index]
    Seeds: waypoints[i+1] or waypoints[1]
    """
    patterns = [
        (r"(waypoints|path|route|points)\[0\]",       "[0]",       "[1]"),
        (r"(waypoints|path|route|points)\[i\]",        "[i]",       "[i+1]"),
        (r"(waypoints|path|route|points)\[-1\]",       "[-1]",      "[-2]"),
    ]
    for f in py_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue
            for pat, old, new in patterns:
                if re.search(pat, line):
                    original = line.rstrip()
                    seeded_line = line.replace(old, new, 1)
                    if seeded_line != line:
                        lines[i] = seeded_line
                        with open(f, "w") as fh:
                            fh.writelines(lines)
                        rel = os.path.relpath(f, project_dir)
                        return SeededDefect(
                            defect_id=f"D{defect_counter:03d}",
                            project=project_name,
                            source="mutation",
                            category="Logic Error",
                            severity="MAJOR",
                            file_path=rel,
                            line_number=i + 1,
                            description=f"MUT-003: Off-by-one in array indexing ({old} → {new})",
                            symptom="Agent targets wrong waypoint, causing path deviation or IndexError",
                            original_code=original,
                            seeded_code=seeded_line.rstrip(),
                        )
    return None


def mutation_corrupt_transform(
    py_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    MUT-004: Corrupt coordinate frame transformation.
    Finds: carla.Transform( / carla.Location(
    Seeds: negates one coordinate argument
    """
    patterns = [
        r"carla\.Location\(x\s*=\s*([^,)]+)",
        r"carla\.Transform\(carla\.Location\(x\s*=\s*([^,)]+)",
    ]
    for f in py_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue
            for pat in patterns:
                m = re.search(pat, line)
                if m:
                    original = line.rstrip()
                    val = m.group(1).strip()
                    # Negate the x value
                    if val.lstrip("-").replace(".", "").isdigit():
                        new_val = str(-float(val)) if "." in val else str(-int(val))
                    else:
                        new_val = f"-({val})"
                    seeded_line = line[:m.start(1)] + new_val + line[m.end(1):]
                    lines[i] = seeded_line
                    with open(f, "w") as fh:
                        fh.writelines(lines)
                    rel = os.path.relpath(f, project_dir)
                    return SeededDefect(
                        defect_id=f"D{defect_counter:03d}",
                        project=project_name,
                        source="mutation",
                        category="API Misuse",
                        severity="CRITICAL",
                        file_path=rel,
                        line_number=i + 1,
                        description="MUT-004: Coordinate frame transformation corrupted (x negated)",
                        symptom="Actor spawned at mirrored location, causing immediate collision or out-of-bounds",
                        original_code=original,
                        seeded_code=seeded_line.rstrip(),
                    )
    return None


def mutation_inject_control_delay(
    py_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    MUT-005: Inject delay in control command publishing.
    Finds: vehicle.apply_control( / self._vehicle.apply_control(
    Seeds: inserts time.sleep(0.5) before the apply_control call
    """
    pattern = r"(vehicle\.apply_control|self\._vehicle\.apply_control|self\.vehicle\.apply_control)"
    for f in py_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue
            if re.search(pattern, line):
                original = line.rstrip()
                indent = len(line) - len(line.lstrip())
                delay_line = " " * indent + "import time; time.sleep(0.5)  # SEEDED MUT-005\n"
                lines.insert(i, delay_line)
                with open(f, "w") as fh:
                    fh.writelines(lines)
                rel = os.path.relpath(f, project_dir)
                return SeededDefect(
                    defect_id=f"D{defect_counter:03d}",
                    project=project_name,
                    source="mutation",
                    category="Robustness Failure",
                    severity="MAJOR",
                    file_path=rel,
                    line_number=i + 1,
                    description="MUT-005: 500ms delay injected before apply_control()",
                    symptom="Control loop latency causes missed braking deadlines and late steering response",
                    original_code=original,
                    seeded_code=delay_line.rstrip(),
                )
    return None


MUTATION_OPERATORS = [
    mutation_remove_sensor_validation,
    mutation_invert_control_logic,
    mutation_off_by_one,
    mutation_corrupt_transform,
    mutation_inject_control_delay,
]


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 3 — Hand-Crafted Scenario Errors (OpenSCENARIO)
# ══════════════════════════════════════════════════════════════════════════════

def find_xosc_files(project_dir: str) -> List[str]:
    """Find all OpenSCENARIO .xosc files in the project."""
    xosc_files = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != ".git"]
        for f in files:
            if f.endswith(".xosc") or f.endswith(".xodr"):
                xosc_files.append(os.path.join(root, f))
    return xosc_files


def handcraft_invalid_actor_reference(
    xosc_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    HC-001: Introduce invalid actor reference in scenario.
    Changes an entityRef to a non-existent actor name.
    """
    for f in xosc_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                content = fh.read()
        except Exception:
            continue
        # Find an entityRef
        m = re.search(r'entityRef="([^"]+)"', content)
        if not m:
            continue
        original_ref = m.group(1)
        seeded_content = content.replace(
            f'entityRef="{original_ref}"',
            f'entityRef="INVALID_ACTOR_XYZ"',
            1
        )
        original_line_num = content[:m.start()].count("\n") + 1
        with open(f, "w") as fh:
            fh.write(seeded_content)
        rel = os.path.relpath(f, project_dir)
        return SeededDefect(
            defect_id=f"D{defect_counter:03d}",
            project=project_name,
            source="handcrafted",
            category="Scenario Specification",
            severity="BLOCKER",
            file_path=rel,
            line_number=original_line_num,
            description="HC-001: entityRef changed to non-existent actor name",
            symptom="ScenarioRunner raises EntityNotFound error at scenario init",
            original_code=f'entityRef="{original_ref}"',
            seeded_code='entityRef="INVALID_ACTOR_XYZ"',
        )
    return None


def handcraft_conflicting_trigger(
    xosc_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    HC-002: Introduce conflicting trigger condition.
    Changes a speed condition value to an impossible threshold.
    """
    for f in xosc_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                content = fh.read()
        except Exception:
            continue
        m = re.search(r'(SpeedCondition[^/]*value=")([^"]+)"', content)
        if not m:
            continue
        original_val = m.group(2)
        # Set speed condition to impossibly high value
        seeded_content = content[:m.start(2)] + "9999.0" + content[m.end(2):]
        line_num = content[:m.start()].count("\n") + 1
        with open(f, "w") as fh:
            fh.write(seeded_content)
        rel = os.path.relpath(f, project_dir)
        return SeededDefect(
            defect_id=f"D{defect_counter:03d}",
            project=project_name,
            source="handcrafted",
            category="Scenario Specification",
            severity="CRITICAL",
            file_path=rel,
            line_number=line_num,
            description="HC-002: SpeedCondition set to impossible threshold (9999.0 m/s)",
            symptom="Trigger condition never fires, scenario hangs indefinitely",
            original_code=f'value="{original_val}"',
            seeded_code='value="9999.0"',
        )
    return None


def handcraft_missing_behavior(
    xosc_files: List[str], project_dir: str,
    defect_counter: int, project_name: str
) -> Optional[SeededDefect]:
    """
    HC-003: Remove a required Act block from scenario.
    Finds an <Act> element and comments it out.
    """
    for f in xosc_files:
        try:
            with open(f, "r", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if "<Act " in line or "<Act>" in line:
                original = line.rstrip()
                lines[i] = f"<!-- SEEDED HC-003 (missing behavior): {line.rstrip()} -->\n"
                with open(f, "w") as fh:
                    fh.writelines(lines)
                rel = os.path.relpath(f, project_dir)
                return SeededDefect(
                    defect_id=f"D{defect_counter:03d}",
                    project=project_name,
                    source="handcrafted",
                    category="Scenario Specification",
                    severity="CRITICAL",
                    file_path=rel,
                    line_number=i + 1,
                    description="HC-003: Required <Act> block commented out",
                    symptom="NPC actors have no behavior, scenario trivially passes with no challenge",
                    original_code=original,
                    seeded_code=lines[i].rstrip(),
                )
    return None


HANDCRAFT_OPERATORS = [
    handcraft_invalid_actor_reference,
    handcraft_conflicting_trigger,
    handcraft_missing_behavior,
]


# ══════════════════════════════════════════════════════════════════════════════
# MAIN SEEDING ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def seed_project(
    project_dir: str,
    project_name: str,
    output_json: str,
    max_defects: int = 10,
    sources: str = "all",
    dry_run: bool = False,
) -> List[SeededDefect]:
    """
    Main entry point. Seeds defects into a project and records ground truth.
    """
    print(f"\n{'='*60}")
    print(f"Seeding project: {project_name}")
    print(f"Directory      : {project_dir}")
    print(f"Max defects    : {max_defects}")
    print(f"Sources        : {sources}")
    print(f"{'='*60}\n")

    if not os.path.isdir(project_dir):
        print(f"ERROR: Project directory not found: {project_dir}")
        return []

    seeded: List[SeededDefect] = []
    counter = 1

    py_files = find_python_files(project_dir)
    xosc_files = find_xosc_files(project_dir)
    print(f"Found {len(py_files)} Python files, {len(xosc_files)} OpenSCENARIO files\n")

    # ── Source 1: Real Bug Commits ────────────────────────────
    if sources in ("all", "real"):
        print("── Source 1: Real Bug Commits ──")
        commits = get_bug_commits(project_dir)
        excluded = 0
        for commit in commits:
            if len(seeded) >= max_defects // 2:
                break
            if not dry_run:
                defect = apply_real_bug_revert(
                    project_dir, commit, counter, project_name)
                if defect:
                    seeded.append(defect)
                    counter += 1
                    print(f"  ✅ {defect.defect_id} [{defect.category}] "
                          f"{defect.description[:60]}")
                else:
                    excluded += 1
            else:
                print(f"  DRY RUN: would process commit {commit['hash'][:8]}: "
                      f"{commit['message'][:60]}")
        print(f"  Excluded {excluded} commits (no viable Python diff)\n")

    # ── Source 2: Mutation Operators ──────────────────────────
    if sources in ("all", "mutation"):
        print("── Source 2: Mutation Operators ──")
        random.shuffle(py_files)   # vary which file gets mutated
        for op in MUTATION_OPERATORS:
            if len(seeded) >= max_defects - len(HANDCRAFT_OPERATORS):
                break
            if not dry_run:
                defect = op(py_files, project_dir, counter, project_name)
                if defect:
                    seeded.append(defect)
                    counter += 1
                    print(f"  ✅ {defect.defect_id} [{defect.category}] "
                          f"{defect.description[:60]}")
                else:
                    print(f"  ⚠️  {op.__name__}: no suitable location found")
            else:
                print(f"  DRY RUN: would apply {op.__name__}")
        print()

    # ── Source 3: Hand-Crafted Scenario Errors ────────────────
    if sources in ("all", "handcrafted"):
        print("── Source 3: Hand-Crafted Scenario Errors ──")
        if not xosc_files:
            print("  ⚠️  No .xosc files found — skipping scenario errors")
        else:
            for op in HANDCRAFT_OPERATORS:
                if len(seeded) >= max_defects:
                    break
                if not dry_run:
                    defect = op(xosc_files, project_dir, counter, project_name)
                    if defect:
                        seeded.append(defect)
                        counter += 1
                        print(f"  ✅ {defect.defect_id} [{defect.category}] "
                              f"{defect.description[:60]}")
                    else:
                        print(f"  ⚠️  {op.__name__}: no suitable location found")
                else:
                    print(f"  DRY RUN: would apply {op.__name__}")
        print()

    # ── Summary ───────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Seeding complete: {len(seeded)} defects seeded")
    by_category = {}
    by_source   = {}
    for d in seeded:
        by_category[d.category] = by_category.get(d.category, 0) + 1
        by_source[d.source]     = by_source.get(d.source, 0)     + 1
    print("\nBy category:")
    for cat, n in sorted(by_category.items()):
        print(f"  {cat:<30} {n}")
    print("\nBy source:")
    for src, n in sorted(by_source.items()):
        print(f"  {src:<20} {n}")
    print(f"{'='*60}\n")

    # ── Save ground truth ─────────────────────────────────────
    if not dry_run:
        os.makedirs(os.path.dirname(output_json)
                    if os.path.dirname(output_json) else ".", exist_ok=True)

        # Load existing ground truth if it exists (append mode)
        existing = []
        if os.path.exists(output_json):
            with open(output_json, "r") as f:
                try:
                    loaded = json.load(f)
                    # Handle both list format and legacy dict format
                    if isinstance(loaded, list):
                        existing = loaded
                    elif isinstance(loaded, dict):
                        # Legacy format - wrap or discard
                        existing = loaded.get("defects", [])
                except json.JSONDecodeError:
                    existing = []

        all_defects = existing + [asdict(d) for d in seeded]

        with open(output_json, "w") as f:
            json.dump(all_defects, f, indent=2)
        print(f"Ground truth saved to: {output_json}")
        print(f"Total defects on record: {len(all_defects)}")

    return seeded


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Automated defect seeding for CARLA projects"
    )
    parser.add_argument("--project-dir",   required=True,
                        help="Path to the CARLA project to seed")
    parser.add_argument("--output",        required=True,
                        help="Path to ground_truth.json output file")
    parser.add_argument("--project-name",  default="unknown",
                        help="Human-readable project name")
    parser.add_argument("--max-defects",   type=int, default=10,
                        help="Max defects to seed (8-12 recommended)")
    parser.add_argument("--sources",       default="all",
                        choices=["all", "real", "mutation", "handcrafted"],
                        help="Which defect sources to use")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Preview what would be seeded without modifying files")
    parser.add_argument("--seed",          type=int, default=42,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    random.seed(args.seed)

    defects = seed_project(
        project_dir=args.project_dir,
        project_name=args.project_name,
        output_json=args.output,
        max_defects=args.max_defects,
        sources=args.sources,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"DRY RUN complete — no files modified")
    else:
        print(f"\nDone. {len(defects)} defects seeded.")
        print("⚠️  Remember: run Stage 1 now to check detection:")
        print(f"   python3 pipeline/stage1_static/carla_custom_rules.py "
              f"--target {args.project_dir} "
              f"--output results/stage1/carla_rules_report.json")


if __name__ == "__main__":
    main()
