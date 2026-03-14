"""
nuXmv Model Checker Runner
============================
Feeds the SMV model into nuXmv, runs LTL model checking,
parses counterexample traces, and saves them as structured JSON
for the next stage (counterexample_to_scenario.py).

A counterexample is a sequence of states that VIOLATES a safety property.
Each one becomes a hazardous test scenario for CARLA.
"""

import subprocess
import os
import re
import json
import argparse
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict


@dataclass
class StateStep:
    """One step in a counterexample trace."""
    step: int
    variables: Dict[str, str] = field(default_factory=dict)


@dataclass
class Counterexample:
    """A full counterexample trace for one violated property."""
    property_name: str
    property_ltl: str
    violated: bool
    steps: List[StateStep] = field(default_factory=list)
    loop_start: Optional[int] = None    # nuXmv marks where the loop begins


@dataclass
class ModelCheckResult:
    property_name: str
    property_ltl: str
    result: str           # "violated" | "satisfied" | "unknown"
    counterexample: Optional[Counterexample] = None
    check_time_sec: float = 0.0


def build_nuxmv_script(smv_file: str, timeout: int) -> str:
    """
    Build the nuXmv command script.
    We use IC3 (property-directed reachability) for LTL checking —
    it's the most efficient algorithm for safety properties.
    """
    return f"""set input_file {smv_file}
go
check_ltlspec
quit
"""


def run_nuxmv(smv_file: str, timeout: int = 300) -> tuple:
    """
    Execute nuXmv on the SMV model file.
    Returns (stdout, stderr, return_code, elapsed_time).
    """
    cmd = ['nuXmv', smv_file]

    print(f"Running nuXmv on: {smv_file}")
    print(f"Timeout: {timeout}s")
    print("This may take a few minutes for complex models...\n")

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - start
        return proc.stdout, proc.stderr, proc.returncode, elapsed
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"nuXmv timed out after {timeout}s")
        return "", "TIMEOUT", -1, elapsed
    except FileNotFoundError:
        return "", "nuXmv not found in PATH", -1, 0.0


def parse_nuxmv_output(stdout: str) -> List[ModelCheckResult]:
    """
    Parse nuXmv output to extract property results and counterexample traces.

    nuXmv output format for a violated property:
        -- specification ... is false
        -- as demonstrated by the following execution sequence
        Trace Description: LTL Counterexample
        Trace Type: Counterexample
          -> State: 1.1 <-
            variable = value
            ...
          -> State: 1.2 <-
            ...
          -- Loop starts here
          -> State: 1.3 <-
            ...
    """
    results = []
    lines = stdout.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Detect property result lines
        if '-- specification' in line and ('is false' in line or 'is true' in line):
            prop_match = re.search(r'-- specification\s+(.+?)\s+is\s+(true|false)', line)
            if not prop_match:
                i += 1
                continue

            prop_ltl = prop_match.group(1).strip()
            result_str = prop_match.group(2)

            # Try to extract property name from LTLSPEC NAME
            formula_to_name = {
	    'time_to_collision > 1500':                'ttc_threshold',
	    'road_zone = urban -> vehicle_speed <= 50': 'urban_speed_limit',
	    'traffic_light_state = red -> brake_applied': 'red_light_compliance',
	    '!collision_occurred':                      'no_collision',
	    'following_distance >= 0':                  'safe_following_distance',
	    'pedestrian_in_path -> brake_applied':      'pedestrian_safety',
	    'vehicle_speed > 0':                        'agent_moves',
	    }
            prop_name = next((v for k, v in formula_to_name.items() if k in prop_ltl), f"property_{len(results)+1}")

            if result_str == 'true':
                results.append(ModelCheckResult(
                    property_name=prop_name,
                    property_ltl=prop_ltl,
                    result='satisfied',
                ))
                i += 1
                continue

            # Property is false — parse the counterexample trace
            ce = Counterexample(
                property_name=prop_name,
                property_ltl=prop_ltl,
                violated=True,
            )

            # Advance to find the trace
            i += 1
            current_step = None
            loop_started = False

            while i < len(lines):
                tline = lines[i].strip()

                # End of this counterexample (next property or end of output)
                if '-- specification' in tline:
                    break

                # Loop marker
                if '-- Loop starts here' in tline:
                    loop_started = True
                    if current_step:
                        ce.loop_start = current_step.step
                    i += 1
                    continue

                # State header: -> State: 1.2 <-
                state_match = re.match(r'-> State: (\d+)\.(\d+) <-', tline)
                if state_match:
                    step_num = int(state_match.group(2))
                    current_step = StateStep(step=step_num)
                    ce.steps.append(current_step)
                    i += 1
                    continue

                # Variable assignment within a state
                var_match = re.match(r'(\S+)\s*=\s*(.+)', tline)
                if var_match and current_step is not None:
                    var_name  = var_match.group(1)
                    var_value = var_match.group(2).strip()
                    current_step.variables[var_name] = var_value
                    i += 1
                    continue

                i += 1

            results.append(ModelCheckResult(
                property_name=prop_name,
                property_ltl=prop_ltl,
                result='violated',
                counterexample=ce,
            ))
            continue

        i += 1

    return results


def main():
    parser = argparse.ArgumentParser(description="Run nuXmv model checker on CARLA agent model")
    parser.add_argument('--model',      required=True, help='Input .smv model file')
    parser.add_argument('--properties', help='LTL properties file (optional, properties embedded in SMV)')
    parser.add_argument('--output',     required=True, help='Output JSON file for counterexamples')
    parser.add_argument('--timeout',    type=int, default=300, help='Timeout in seconds per run')
    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"ERROR: Model file not found: {args.model}")
        exit(1)

    # Run nuXmv
    stdout, stderr, returncode, elapsed = run_nuxmv(args.model, args.timeout)

    if returncode == -1 and stderr == "nuXmv not found in PATH":
        print("ERROR: nuXmv is not installed or not in PATH")
        print("Install it from: https://nuxmv.fbk.eu/downloads.html")
        exit(1)

    print(f"nuXmv finished in {elapsed:.1f}s (return code: {returncode})")

    # Parse results
    results = parse_nuxmv_output(stdout)

    if not results:
        print("WARNING: No property results found in nuXmv output.")
        print("Raw output preview:")
        print(stdout[:500])

    # Summarize
    violated  = [r for r in results if r.result == 'violated']
    satisfied = [r for r in results if r.result == 'satisfied']

    print(f"\nModel checking results:")
    print(f"  Properties checked  : {len(results)}")
    print(f"  Violated (→ scenarios): {len(violated)}")
    print(f"  Satisfied           : {len(satisfied)}")

    for r in violated:
        steps = len(r.counterexample.steps) if r.counterexample else 0
        print(f"  ❌ {r.property_name} — counterexample has {steps} steps")
    for r in satisfied:
        print(f"  ✅ {r.property_name}")

    # Build output
    output = {
        "model_file": args.model,
        "check_time_sec": elapsed,
        "properties_checked": len(results),
        "violations_found": len(violated),
        "failures": [],
        "raw_results": [],
    }

    for r in results:
        raw = {
            "property_name": r.property_name,
            "property_ltl": r.property_ltl,
            "result": r.result,
            "check_time_sec": r.check_time_sec,
        }
        if r.counterexample:
            raw["counterexample"] = asdict(r.counterexample)
            # Add to failures list for metrics aggregator
            output["failures"].append({
                "property_name": r.property_name,
                "file": args.model,
                "line": 0,
                "steps": len(r.counterexample.steps),
                "category": map_property_to_category(r.property_name),
            })
        output["raw_results"].append(raw)

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else '.', exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\nCounterexamples saved to: {args.output}")


def map_property_to_category(prop_name: str) -> str:
    """Map property names to defect taxonomy categories."""
    mapping = {
        'ttc_threshold':         'Robustness Failure',
        'urban_speed_limit':     'Logic Error',
        'red_light_compliance':  'Logic Error',
        'no_collision':          'Robustness Failure',
        'safe_following_distance': 'Logic Error',
        'pedestrian_safety':     'Robustness Failure',
        'agent_moves':           'Logic Error',
    }
    return mapping.get(prop_name, 'Logic Error')


if __name__ == '__main__':
    main()

