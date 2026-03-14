"""
Agent Model Extractor
======================
Reads CARLA Python agent code and extracts a symbolic model
in SMV format that nuXmv can reason about.

What it does:
  1. Scans agent Python files for control logic patterns
  2. Parses actual AST comparison conditions (operator + threshold)
  3. Builds an SMV transition system reflecting the real control logic
  4. Writes a .smv file that nuXmv can model-check against safety properties

Key improvement over v1: uses AST-level comparison extraction so that
mutations (e.g. inverting > to <) produce a different SMV model and
different nuXmv counterexamples.
"""

import ast
import os
import re
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import operator as op

# ── Variable name patterns ─────────────────────────────────────────────────────

SPEED_NAMES = re.compile(
    r'\b(speed|velocity|target_speed|v_ego|current_speed|ego_speed)\b', re.I
)
DISTANCE_NAMES = re.compile(
    r'\b(distance|dist|following_dist|gap|headway)\b', re.I
)
TTC_NAMES = re.compile(
    r'\b(ttc|time_to_collision)\b', re.I
)
TRAFFIC_LIGHT_NAMES = re.compile(
    r'\b(traffic_light|red_light|light_state|tl_state|is_red)\b', re.I
)
BRAKE_NAMES = re.compile(
    r'\b(brake|braking)\b', re.I
)
THROTTLE_NAMES = re.compile(
    r'\b(throttle|accelerat)\b', re.I
)


# ── AST operator helpers ───────────────────────────────────────────────────────

def op_to_str(node) -> str:
    """Convert an AST comparison operator to a string symbol."""
    mapping = {
        ast.Gt:  '>',
        ast.Lt:  '<',
        ast.GtE: '>=',
        ast.LtE: '<=',
        ast.Eq:  '=',
        ast.NotEq: '!=',
    }
    return mapping.get(type(node), '?')


def invert_op(op_str: str) -> str:
    """Return the logical inverse of a comparison operator."""
    return {'>': '<=', '<': '>=', '>=': '<', '<=': '>', '=': '!=', '!=': '='}.get(op_str, op_str)


# ── Extracted condition dataclass ──────────────────────────────────────────────

@dataclass
class Condition:
    """
    A single extracted comparison condition from the agent source.
    e.g.  'speed > 50'  →  variable='speed', operator='>', threshold=50.0
    """
    variable: str       # canonical name: 'speed', 'distance', 'ttc', 'traffic_light'
    operator: str       # '>', '<', '>=', '<=', '='
    threshold: float    # numeric threshold value
    in_brake_context: bool = False    # True if this condition leads to braking
    in_throttle_context: bool = False # True if this condition leads to throttle


@dataclass
class AgentFeatures:
    """All features extracted from agent Python code."""
    has_speed_control: bool = False
    has_distance_check: bool = False
    has_traffic_light_check: bool = False
    has_lane_change: bool = False
    has_brake_control: bool = False
    has_sensor_fusion: bool = False

    # Extracted conditions from actual AST comparisons
    conditions: List[Condition] = field(default_factory=list)

    # Fallback scalar values (used if no conditions found)
    max_speed_value: Optional[float] = None
    min_distance_value: Optional[float] = None

    files_analyzed: List[str] = field(default_factory=list)
    control_functions: List[str] = field(default_factory=list)


# ── AST visitor ───────────────────────────────────────────────────────────────

class ConditionExtractor(ast.NodeVisitor):
    """
    Walks the AST and extracts comparison conditions from if-statements
    that involve driving-related variables.

    For each If node, it:
      1. Checks whether the test contains a known variable pattern
      2. Extracts the operator and numeric threshold
      3. Checks the body for brake/throttle calls to determine context
    """

    def __init__(self):
        self.conditions: List[Condition] = []

    def _classify_name(self, name: str) -> Optional[str]:
        """Map a variable name to a canonical category."""
        name_lower = name.lower()
        if SPEED_NAMES.search(name_lower):
            return 'speed'
        if DISTANCE_NAMES.search(name_lower):
            return 'distance'
        if TTC_NAMES.search(name_lower):
            return 'ttc'
        if TRAFFIC_LIGHT_NAMES.search(name_lower):
            return 'traffic_light'
        return None

    def _body_has_brake(self, stmts) -> bool:
        """Return True if any statement in the body sets brake or calls a brake function."""
        body_src = ast.dump(ast.Module(body=stmts, type_ignores=[]))
        return bool(BRAKE_NAMES.search(body_src))

    def _body_has_throttle(self, stmts) -> bool:
        """Return True if any statement in the body sets throttle."""
        body_src = ast.dump(ast.Module(body=stmts, type_ignores=[]))
        return bool(THROTTLE_NAMES.search(body_src))

    def _node_name(self, node) -> Optional[str]:
        """Extract a string name from a Name or Attribute AST node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _extract_from_compare(self, compare: ast.Compare,
                               brake_ctx: bool, throttle_ctx: bool):
        """
        Extract conditions from a Compare node.
        Handles three forms:
          1. var op literal       e.g.  current_speed < 50
          2. literal op var       e.g.  50 < current_speed
          3. var op var           e.g.  current_speed < self._target_speed
             For var-vs-var we record threshold=-1 (sentinel) meaning
             "compare to target_speed variable in SMV". The operator
             direction is preserved so mutations (> vs <) change the model.
        """
        left = compare.left
        for cmp_op, right in zip(compare.ops, compare.comparators):
            op_str = op_to_str(cmp_op)

            # Case 1: Name/Attribute op Number
            if isinstance(right, ast.Constant) and isinstance(right.value, (int, float)):
                var_name = self._classify_name(self._node_name(left) or '')
                if var_name:
                    self.conditions.append(Condition(
                        variable=var_name,
                        operator=op_str,
                        threshold=float(right.value),
                        in_brake_context=brake_ctx,
                        in_throttle_context=throttle_ctx,
                    ))

            # Case 2: Number op Name/Attribute  (e.g.  50 < speed)
            elif isinstance(left, ast.Constant) and isinstance(left.value, (int, float)):
                var_name = self._classify_name(self._node_name(right) or '')
                if var_name:
                    self.conditions.append(Condition(
                        variable=var_name,
                        operator=invert_op(op_str),
                        threshold=float(left.value),
                        in_brake_context=brake_ctx,
                        in_throttle_context=throttle_ctx,
                    ))

            # Case 3: var op var  (e.g. current_speed < self._target_speed)
            # Captures D007-style mutations where the threshold is another variable.
            # We record threshold=-1 as a sentinel meaning "use target_speed in SMV".
            else:
                left_name  = self._node_name(left)
                right_name = self._node_name(right)
                lvar = self._classify_name(left_name or '')
                rvar = self._classify_name(right_name or '')
                if lvar == 'speed' or rvar == 'speed':
                    canonical = 'speed'
                    final_op  = op_str if lvar == 'speed' else invert_op(op_str)
                    self.conditions.append(Condition(
                        variable=canonical,
                        operator=final_op,
                        threshold=-1.0,  # sentinel: compare against target_speed SMV var
                        in_brake_context=brake_ctx,
                        in_throttle_context=throttle_ctx,
                    ))

    def visit_If(self, node: ast.If):
        """Visit every if-statement and extract conditions from its test."""
        brake_ctx    = self._body_has_brake(node.body)
        throttle_ctx = self._body_has_throttle(node.body)

        # Walk all Compare nodes inside the test expression
        for subnode in ast.walk(node.test):
            if isinstance(subnode, ast.Compare):
                self._extract_from_compare(subnode, brake_ctx, throttle_ctx)

        # Continue walking into nested ifs
        self.generic_visit(node)


# ── File analysis ──────────────────────────────────────────────────────────────

def extract_control_functions(tree: ast.AST) -> List[str]:
    """Find function names that likely contain control logic."""
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name.lower()
            if any(kw in name for kw in ('run', 'step', 'act', 'control', 'plan', 'decide')):
                funcs.append(node.name)
    return funcs


def analyze_python_file(filepath: str) -> AgentFeatures:
    """Analyze a single Python file for agent features and conditions."""
    features = AgentFeatures()
    features.files_analyzed.append(filepath)

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            source = f.read()

        # Boolean feature flags (fast regex pass)
        features.has_speed_control       = bool(SPEED_NAMES.search(source))
        features.has_distance_check      = bool(DISTANCE_NAMES.search(source))
        features.has_traffic_light_check = bool(TRAFFIC_LIGHT_NAMES.search(source))
        features.has_lane_change         = bool(re.search(r'\blane_change\b', source, re.I))
        features.has_brake_control       = bool(BRAKE_NAMES.search(source))
        features.has_sensor_fusion       = bool(re.search(
            r'\b(camera|lidar|gnss|imu|radar|sensor_data)\b', source, re.I))

        # AST pass — extract actual comparison conditions
        try:
            tree = ast.parse(source)
            extractor = ConditionExtractor()
            extractor.visit(tree)
            features.conditions = extractor.conditions
            features.control_functions = extract_control_functions(tree)
        except SyntaxError:
            pass

        # Fallback scalar values from conditions
        speed_vals = [c.threshold for c in features.conditions if c.variable == 'speed']
        dist_vals  = [c.threshold for c in features.conditions if c.variable == 'distance'
                      and c.threshold < 1000]
        if speed_vals:
            features.max_speed_value = max(speed_vals)
        if dist_vals:
            features.min_distance_value = min(dist_vals)

    except Exception as e:
        print(f"  Warning: could not analyze {filepath}: {e}")

    return features


def merge_features(features_list: List[AgentFeatures]) -> AgentFeatures:
    """Merge features from multiple files into one combined model."""
    merged = AgentFeatures()
    merged.has_speed_control       = any(f.has_speed_control for f in features_list)
    merged.has_distance_check      = any(f.has_distance_check for f in features_list)
    merged.has_traffic_light_check = any(f.has_traffic_light_check for f in features_list)
    merged.has_lane_change         = any(f.has_lane_change for f in features_list)
    merged.has_brake_control       = any(f.has_brake_control for f in features_list)
    merged.has_sensor_fusion       = any(f.has_sensor_fusion for f in features_list)

    # Merge all extracted conditions
    merged.conditions = [c for f in features_list for c in f.conditions]

    speed_vals = [f.max_speed_value for f in features_list if f.max_speed_value]
    dist_vals  = [f.min_distance_value for f in features_list if f.min_distance_value]
    merged.max_speed_value    = max(speed_vals) if speed_vals else 50.0
    merged.min_distance_value = min(dist_vals)  if dist_vals  else 5.0

    merged.files_analyzed    = [fp for f in features_list for fp in f.files_analyzed]
    merged.control_functions = [fn for f in features_list for fn in f.control_functions]
    return merged


# ── SMV generation ─────────────────────────────────────────────────────────────

def _pick_condition(conditions: List[Condition], variable: str,
                    context: str) -> Optional[Condition]:
    """
    Pick the most representative condition for a variable in a given context.
    context: 'brake' or 'throttle'
    Returns the condition whose operator and threshold best represent the
    agent's actual decision logic for that variable.
    """
    ctx_field = 'in_brake_context' if context == 'brake' else 'in_throttle_context'
    # Prefer conditions that are in the right context
    ctx_conds = [c for c in conditions
                 if c.variable == variable and getattr(c, ctx_field)]
    if ctx_conds:
        # Return the one with the most common threshold (mode)
        from collections import Counter
        threshold_counts = Counter(c.threshold for c in ctx_conds)
        best_threshold = threshold_counts.most_common(1)[0][0]
        for c in ctx_conds:
            if c.threshold == best_threshold:
                return c
    # Fall back to any condition for this variable
    any_conds = [c for c in conditions if c.variable == variable]
    return any_conds[0] if any_conds else None


def _smv_condition(variable: str, op_str: str, threshold: float,
                   smv_var: str) -> str:
    """
    Render a condition as an SMV boolean expression.
    Clamps threshold to a safe integer for SMV range variables.
    """
    t = int(threshold)
    return f"{smv_var} {op_str} {t}"


def generate_smv_model(features: AgentFeatures, project_name: str) -> str:
    """
    Generate an SMV model from extracted agent features and conditions.

    The key difference from v1: transitions are built from the actual
    extracted comparison conditions, not fixed hardcoded rules.
    This means mutations that change operators or thresholds produce
    a structurally different SMV model with different counterexamples.
    """
    conditions = features.conditions

    # ── Speed threshold for urban zone ────────────────────────────────────────
    speed_cond = _pick_condition(conditions, 'speed', 'throttle')
    if not speed_cond:
        speed_cond = _pick_condition(conditions, 'speed', 'brake')

    if speed_cond and speed_cond.threshold == -1.0:
        # var-vs-var comparison: current_speed OP target_speed
        # Introduce target_speed as an SMV variable and encode the operator.
        # D007 flips the operator (< becomes >) which changes this guard.
        speed_op             = speed_cond.operator
        speed_limit          = int(features.max_speed_value or 50)
        throttle_speed_guard = f"vehicle_speed {speed_op} target_speed"
        use_target_speed_var = True
    elif speed_cond and speed_cond.threshold >= 0:
        speed_limit          = int(speed_cond.threshold)
        speed_op             = speed_cond.operator
        throttle_speed_guard = f"vehicle_speed {speed_op} {speed_limit}"
        use_target_speed_var = False
    else:
        speed_limit          = int(features.max_speed_value or 50)
        speed_op             = '<'
        throttle_speed_guard = f"vehicle_speed < target_speed"
        use_target_speed_var = True

    urban_speed_limit_val = min(speed_limit, 50)

    # ── Distance / following distance threshold ───────────────────────────────
    dist_cond = _pick_condition(conditions, 'distance', 'brake')
    if dist_cond:
        min_dist  = int(dist_cond.threshold)
        dist_op   = dist_cond.operator               # e.g. '<' means brake when dist < X
        brake_dist_guard = f"following_distance {dist_op} {min_dist}"
    else:
        min_dist         = int(features.min_distance_value or 5)
        dist_op          = '<'
        brake_dist_guard = f"following_distance < {min_dist}"

    # ── TTC threshold ─────────────────────────────────────────────────────────
    ttc_cond = _pick_condition(conditions, 'ttc', 'brake')
    if ttc_cond:
        ttc_thresh    = int(ttc_cond.threshold)
        ttc_op        = ttc_cond.operator
        ttc_smv_guard = f"time_to_collision {ttc_op} {ttc_thresh}"
    else:
        ttc_thresh    = 1500
        ttc_op        = '<'
        ttc_smv_guard = f"time_to_collision < {ttc_thresh}"

    max_speed = max(speed_limit + 20, 80)

    # ── Summarise what was extracted (for comments in SMV) ───────────────────
    n_speed = sum(1 for c in conditions if c.variable == 'speed')
    n_dist  = sum(1 for c in conditions if c.variable == 'distance')
    n_ttc   = sum(1 for c in conditions if c.variable == 'ttc')
    n_tl    = sum(1 for c in conditions if c.variable == 'traffic_light')

    smv = f"""-- ============================================================
-- SMV Model for CARLA Agent: {project_name}
-- Auto-generated by extract_agent_model.py (AST-aware version)
-- Files analyzed : {len(features.files_analyzed)}
-- Conditions extracted:
--   speed comparisons    : {n_speed}
--   distance comparisons : {n_dist}
--   TTC comparisons      : {n_ttc}
--   traffic-light checks : {n_tl}
--
-- Key extracted thresholds (reflect actual agent code):
--   Speed throttle guard : vehicle_speed {speed_op} {"target_speed" if use_target_speed_var else speed_limit}
--   Distance brake guard : following_distance {dist_op} {min_dist}
--   TTC brake guard      : time_to_collision {ttc_op} {ttc_thresh}
-- ============================================================
MODULE main
VAR
  -- Vehicle speed in km/h
  vehicle_speed      : 0..{max_speed};
  -- Target speed set by agent (used when control logic compares current vs target)
  {"target_speed    : 0.." + str(max_speed) + ";" if use_target_speed_var else "-- target_speed  : not used (literal threshold extracted)"}
  -- Following distance to lead vehicle in metres
  following_distance : 0..100;
  -- Time to collision in milliseconds (0 = collision imminent)
  time_to_collision  : 0..10000;
  -- Traffic light state
  traffic_light_state : {{red, yellow, green, none}};
  -- Current road zone
  road_zone : {{urban, highway, intersection, parking}};
  -- Agent control outputs
  throttle_applied   : boolean;
  brake_applied      : boolean;
  -- Lane state
  in_correct_lane    : boolean;
  lane_change_active : boolean;
  -- Collision flag
  collision_occurred : boolean;
  -- Pedestrian in path
  pedestrian_in_path : boolean;
  -- Sensor availability
  sensor_available   : boolean;

ASSIGN
  -- ── Initial state ─────────────────────────────────────────
  init(vehicle_speed)        := 0;
  init(following_distance)   := 50;
  init(time_to_collision)    := 10000;
  init(traffic_light_state)  := green;
  init(road_zone)            := urban;
  init(throttle_applied)     := FALSE;
  init(brake_applied)        := FALSE;
  init(in_correct_lane)      := TRUE;
  init(lane_change_active)   := FALSE;
  init(collision_occurred)   := FALSE;
  init(pedestrian_in_path)   := FALSE;
  init(sensor_available)     := TRUE;
{"  init(target_speed)         := 30;" if use_target_speed_var else ""}
  -- ── Speed transition (reflects extracted speed operator/threshold) ──
  next(vehicle_speed) :=
    case
      brake_applied & vehicle_speed > 0              : vehicle_speed - 1;
      throttle_applied & {throttle_speed_guard}
        & road_zone = urban                          : vehicle_speed;
      throttle_applied & road_zone = urban
        & vehicle_speed < {urban_speed_limit_val}   : vehicle_speed + 1;
      throttle_applied & road_zone = highway
        & vehicle_speed < {max_speed}               : vehicle_speed + 1;
      TRUE                                            : vehicle_speed;
    esac;

  -- ── Following distance transition ─────────────────────────
  next(following_distance) :=
    case
      brake_applied & following_distance < 100  : following_distance + 1;
      throttle_applied & following_distance > 0 : following_distance - 1;
      TRUE                                       : following_distance;
    esac;

  -- ── TTC transition ────────────────────────────────────────
  next(time_to_collision) :=
    case
      vehicle_speed = 0             : 10000;
      following_distance = 0        : 0;
      following_distance < 5        : 500;
      following_distance < 10       : 1000;
      following_distance < 20       : 2000;
      TRUE                          : 5000;
    esac;

  -- ── Environment (non-deterministic) ──────────────────────
  next(traffic_light_state) :=
    case
      traffic_light_state = green  : {{green, yellow}};
      traffic_light_state = yellow : {{yellow, red}};
      traffic_light_state = red    : {{red, green}};
      TRUE                         : none;
    esac;

  next(road_zone)            := {{urban, highway, intersection, parking}};
  next(pedestrian_in_path)   := {{TRUE, FALSE}};
  next(sensor_available)     := {{TRUE, FALSE}};

  -- ── Throttle transition (derived from extracted speed condition) ──
  -- If agent code says  speed > threshold → brake  then throttle is
  -- blocked in that region.  A mutation flipping > to < shifts this
  -- guard and changes reachable states, producing different counterexamples.
  next(throttle_applied) :=
    case
      traffic_light_state = red  : FALSE;
      pedestrian_in_path         : FALSE;
      collision_occurred         : FALSE;
      {throttle_speed_guard}     : FALSE;
      TRUE                       : {{TRUE, FALSE}};
    esac;

  -- ── Brake transition (derived from extracted distance/TTC condition) ──
  next(brake_applied) :=
    case
      traffic_light_state = red  : TRUE;
      pedestrian_in_path         : TRUE;
      {brake_dist_guard}         : TRUE;
      {ttc_smv_guard}            : TRUE;
      TRUE                       : {{TRUE, FALSE}};
    esac;

  -- ── Collision ─────────────────────────────────────────────
  next(collision_occurred) :=
    case
      time_to_collision = 0  : TRUE;
      following_distance = 0 : TRUE;
      TRUE                   : collision_occurred;
    esac;

  -- ── Lane state ────────────────────────────────────────────
  next(in_correct_lane) :=
    case
      lane_change_active : {{TRUE, FALSE}};
      TRUE               : in_correct_lane;
    esac;
  next(lane_change_active) := {{TRUE, FALSE}};
{"  -- target_speed changes non-deterministically (set by mission planner)" if use_target_speed_var else ""}
{"  next(target_speed) := {0.." + str(max_speed) + "};" if use_target_speed_var else ""}

-- ============================================================
-- SAFETY PROPERTIES (LTL)
-- Counterexamples are automatically converted to test scenarios
-- ============================================================

-- P1: TTC must never drop below extracted threshold
LTLSPEC NAME ttc_threshold :=
  G (time_to_collision > {ttc_thresh})

-- P2: Speed must not exceed limit in urban zones
LTLSPEC NAME urban_speed_limit :=
  G (road_zone = urban -> vehicle_speed <= {urban_speed_limit_val})

-- P3: Vehicle must brake at red light
LTLSPEC NAME red_light_compliance :=
  G (traffic_light_state = red -> brake_applied)

-- P4: Collision must never occur
LTLSPEC NAME no_collision :=
  G (!collision_occurred)

-- P5: Following distance must stay above extracted minimum
LTLSPEC NAME safe_following_distance :=
  G (following_distance >= {min_dist})

-- P6: Vehicle must stop when pedestrian is in path
LTLSPEC NAME pedestrian_safety :=
  G (pedestrian_in_path -> brake_applied)

-- P7: Agent must eventually reach non-zero speed (liveness)
LTLSPEC NAME agent_moves :=
  F (vehicle_speed > 0)
"""
    return smv


# ── File discovery ─────────────────────────────────────────────────────────────

def find_agent_files(agent_dir: str) -> List[str]:
    """Find Python files likely to contain agent logic."""
    exclude = {'.git', '__pycache__', '.venv', 'venv', 'test', 'tests', 'docs'}
    agent_files = []
    for root, dirs, files in os.walk(agent_dir):
        dirs[:] = [d for d in dirs if d not in exclude]
        for fname in files:
            if fname.endswith('.py'):
                path = os.path.join(root, fname)
                if any(kw in fname.lower() for kw in
                       ('agent', 'control', 'plan', 'nav', 'drive', 'autopilot')):
                    agent_files.insert(0, path)
                else:
                    agent_files.append(path)
    return agent_files


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract SMV model from CARLA agent code (AST-aware)")
    parser.add_argument('--agent-dir', required=True,
                        help='Directory containing agent Python code')
    parser.add_argument('--output',    required=True,
                        help='Output .smv file path')
    parser.add_argument('--project',   default='carla_agent',
                        help='Project name for model header')
    parser.add_argument('--max-files', type=int, default=30,
                        help='Max files to analyze')
    args = parser.parse_args()

    print(f"Extracting agent model from: {args.agent_dir}")
    agent_files = find_agent_files(args.agent_dir)[:args.max_files]
    print(f"Analyzing {len(agent_files)} agent-related Python files...")

    features_list = [analyze_python_file(fp) for fp in agent_files]
    merged = merge_features(features_list)

    # Count conditions per variable
    by_var = {}
    for c in merged.conditions:
        by_var.setdefault(c.variable, []).append(c)

    print(f"\nExtracted features:")
    print(f"  Speed control      : {merged.has_speed_control}")
    print(f"  Distance checks    : {merged.has_distance_check}")
    print(f"  Traffic light logic: {merged.has_traffic_light_check}")
    print(f"  Lane change logic  : {merged.has_lane_change}")
    print(f"  Sensor fusion      : {merged.has_sensor_fusion}")
    print(f"  Max speed found    : {merged.max_speed_value} km/h")
    print(f"  Min distance found : {merged.min_distance_value} m")
    print(f"  Control functions  : {merged.control_functions[:5]}")
    print(f"\nExtracted conditions ({len(merged.conditions)} total):")
    for var, conds in by_var.items():
        for c in conds[:3]:  # show up to 3 per variable
            ctx = 'brake' if c.in_brake_context else ('throttle' if c.in_throttle_context else 'other')
            print(f"  [{ctx:8s}]  {c.variable} {c.operator} {c.threshold}")

    smv_model = generate_smv_model(merged, args.project)

    os.makedirs(
        os.path.dirname(args.output) if os.path.dirname(args.output) else '.',
        exist_ok=True)
    with open(args.output, 'w') as f:
        f.write(smv_model)
    print(f"\nSMV model written to: {args.output}")

    # Save features JSON
    features_json = args.output.replace('.smv', '_features.json')
    with open(features_json, 'w') as f:
        json.dump({
            'project': args.project,
            'files_analyzed': len(agent_files),
            'features': {
                'has_speed_control':       merged.has_speed_control,
                'has_distance_check':      merged.has_distance_check,
                'has_traffic_light_check': merged.has_traffic_light_check,
                'has_lane_change':         merged.has_lane_change,
                'has_brake_control':       merged.has_brake_control,
                'has_sensor_fusion':       merged.has_sensor_fusion,
                'max_speed_value':         merged.max_speed_value,
                'min_distance_value':      merged.min_distance_value,
                'control_functions':       merged.control_functions,
            },
            'extracted_conditions': [
                {
                    'variable':           c.variable,
                    'operator':           c.operator,
                    'threshold':          c.threshold,
                    'in_brake_context':   c.in_brake_context,
                    'in_throttle_context': c.in_throttle_context,
                }
                for c in merged.conditions
            ],
        }, f, indent=2)
    print(f"Features JSON saved to: {features_json}")


if __name__ == '__main__':
    main()
