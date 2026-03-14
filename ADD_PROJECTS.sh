# Adding Projects 2 and 3 to the Pipeline
# =========================================
# Run these commands from ~/Desktop/carla-cicd-pipeline/
#
# We use two projects that are:
#   - Different agent types from scenario_runner (which is classical planning)
#   - Small enough to clone and seed quickly
#   - Have Python agent code with CARLA control logic
#
# Project 2: carla-simulator/leaderboard  (perception-based planning agent)
# Project 3: carla-simulator/carla PythonAPI agents (classical behavior agent)
# Both are from the official CARLA org so licensing is clean.

# ─────────────────────────────────────────────────────────────────────────────
# PROJECT 2: CARLA Leaderboard
# Agent type: perception-based
# ─────────────────────────────────────────────────────────────────────────────

cd ~/Desktop/carla-cicd-pipeline/test_projects

# Clone leaderboard if not already there
git clone https://github.com/carla-simulator/leaderboard.git leaderboard_backup
cp -r leaderboard_backup leaderboard

# Verify Python files exist
find leaderboard/team_code -name "*.py" 2>/dev/null | head -10
# If team_code doesn't exist, check:
find leaderboard -name "*.py" | grep -i agent | head -10

# ─────────────────────────────────────────────────────────────────────────────
# PROJECT 3: CARLA PythonAPI agents (behavior_agent)
# Agent type: classical behavior with PID control
# ─────────────────────────────────────────────────────────────────────────────

# We extract just the agents folder from the main CARLA repo
git clone --depth=1 --filter=blob:none --sparse \
    https://github.com/carla-simulator/carla.git carla_agents_backup
cd carla_agents_backup
git sparse-checkout set PythonAPI/carla/agents
cd ..
cp -r carla_agents_backup carla_agents

# ─────────────────────────────────────────────────────────────────────────────
# SEEDING PROJECT 2 (leaderboard)
# Target files for defects:
# ─────────────────────────────────────────────────────────────────────────────

# First run Stage 1 on the clean version to confirm it's clean
python3 pipeline/stage1_static/carla_custom_rules.py \
  --target test_projects/leaderboard_backup/ \
  --output results/leaderboard/stage1_clean.json

# Then seed defects — 4 defects minimum across 3 categories:
# D_LB_001: API Misuse — add world.tick() inside a sensor callback
# D_LB_002: Resource Management — spawn actor without destroy()
# D_LB_003: Logic Error — invert a speed/control comparison
# D_LB_004: Robustness Failure — remove a None check on sensor data

# Find the right files first:
grep -rn "world.tick\|apply_control\|sensor_data\|destroy()" \
    test_projects/leaderboard/ --include="*.py" | head -20

# ─────────────────────────────────────────────────────────────────────────────
# SEEDING PROJECT 3 (carla_agents)
# Target files: behavior_agent.py, basic_agent.py, controller.py
# ─────────────────────────────────────────────────────────────────────────────

# Clean run first
python3 pipeline/stage1_static/carla_custom_rules.py \
  --target test_projects/carla_agents_backup/ \
  --output results/carla_agents/stage1_clean.json

# Seed defects:
# D_CA_001: API Misuse — world.tick() in wrong context
# D_CA_002: Resource Management — missing destroy() on spawned actor
# D_CA_003: Logic Error — flip speed_limit comparison in behavior_agent.py

# Specific mutations for carla_agents behavior_agent.py:
# Line containing: if self._speed > self._behavior.max_speed
# Mutation: flip > to <
grep -n "max_speed\|speed_limit\|target_speed" \
    test_projects/carla_agents/PythonAPI/carla/agents/navigation/behavior_agent.py | head -20

# ─────────────────────────────────────────────────────────────────────────────
# RUNNING THE FULL PIPELINE ON ALL 3 PROJECTS
# ─────────────────────────────────────────────────────────────────────────────

# After seeding, run for each project:

for PROJECT in scenario_runner leaderboard carla_agents; do
  echo "=== Running pipeline on $PROJECT ==="

  # Stage 1
  python3 pipeline/stage1_static/carla_custom_rules.py \
    --target test_projects/${PROJECT}/ \
    --output results/${PROJECT}/stage1_report.json

  # Stage 2
  python3 pipeline/stage2_formal/extract_agent_model.py \
    --agent-dir test_projects/${PROJECT} \
    --output results/${PROJECT}/agent_model.smv \
    --project ${PROJECT}

  python3 pipeline/stage2_formal/run_model_checker.py \
    --model results/${PROJECT}/agent_model.smv \
    --output results/${PROJECT}/counterexamples.json \
    --timeout 300

done

# ─────────────────────────────────────────────────────────────────────────────
# UPDATING ground_truth.json
# ─────────────────────────────────────────────────────────────────────────────
# After seeding all projects, update defect_seeding/ground_truth.json
# to add the new defects for leaderboard and carla_agents.
# Format for each new entry:
# {
#   "id": "D_LB_001",
#   "project": "leaderboard",
#   "category": "API Misuse",
#   "source": "mutation",
#   "file": "srunner/...",   <- relative path within project
#   "line": 42,
#   "description": "world.tick() called inside sensor callback",
#   "expected_symptom": "deadlock in synchronous mode",
#   "detected_by_stage1": null,   <- fill in after running
#   "detected_by_stage2": null
# }
