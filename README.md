# CARLA CI/CD Validation Pipeline

An integrated CI/CD pipeline for empirically comparing automated
validation techniques for CARLA-based autonomous driving systems.

Built for: Masters research in Software Development Tools & Methods  
University: Ontario Tech University

---

## Pipeline Architecture

```
Push to GitHub
      │
      ▼
┌─────────────────────────────┐
│  Stage 1: Static Analysis   │  ← flake8, pylint, bandit +
│  (no simulation, seconds)   │    CARLA custom rules (CRL-001..007)
└────────────┬────────────────┘
             │ PASS?
             ▼
┌─────────────────────────────┐
│  Stage 2: Formal Synthesis  │  ← VIVAS + nuXmv model checking
│  (symbolic, ~minutes)       │    → OpenSCENARIO counterexamples
└────────────┬────────────────┘
             │ PASS?
             ▼
┌─────────────────────────────┐
│  Stage 3: LLM Generation    │  ← GPT-4 Turbo (contingent)
│  (generative, ~minutes)     │    generate-verify-refine loop
└────────────┬────────────────┘
             │
             ▼
┌─────────────────────────────┐
│  Metrics Aggregation        │  ← Detection rate, precision,
│                             │    recall, cost, overlap report
└─────────────────────────────┘
```

Each stage is **gated**: failure at Stage 1 blocks Stage 2 from running,
saving expensive GPU simulation time.

---

## Quick Start

### Run locally with Docker Compose

```bash
# Stage 1 only (fast, no CARLA needed)
docker compose up stage1

# Stages 1 + 2 (requires nuXmv)
docker compose up stage1 stage2

# Full pipeline including LLM stage
OPENAI_API_KEY=sk-... docker compose --profile llm up

# Collect metrics after stages complete
docker compose up metrics
```

### Run via GitHub Actions

Push to `main` or `develop` — the pipeline triggers automatically.

To enable Stage 3 (LLM), add these in your repo settings:
- **Secret**: `OPENAI_API_KEY`  
- **Variable**: `ENABLE_LLM_STAGE` = `true`

---

## Project Structure

```
.github/workflows/
  validation-pipeline.yml     # Main CI/CD workflow

pipeline/
  stage1_static/
    carla_custom_rules.py     # CARLA-specific AST analysis rules
    aggregate_results.py      # Combines flake8/pylint/bandit/carla
    print_failure_report.py   # Developer-readable failure output
    Dockerfile

  stage2_formal/
    safety_properties.ltl     # LTL specs for nuXmv
    run_model_checker.py      # VIVAS integration (to implement)
    Dockerfile

  stage3_llm/
    safety_requirements.txt   # Natural language requirements
    generate_scenarios.py     # GPT-4 scenario generation (to implement)
    validate_scenarios.py     # Physical plausibility checks (to implement)
    Dockerfile

defect_seeding/
  ground_truth.json           # Ground truth labels for seeded defects
  seed_defects.py             # (to implement) automated seeding scripts

metrics/
  aggregate_pipeline_metrics.py   # Cross-stage detection matrix
  generate_comparison_report.py   # Markdown comparison report

docker-compose.yml            # Local dev environment
```

---

## Defect Taxonomy

| Category | Description | Primary Detector |
|----------|-------------|-----------------|
| API Misuse | Incorrect CARLA function calls, wrong parameters | Stage 1 |
| Resource Management | Actor leaks, missing destroy() | Stage 1 |
| Scenario Specification | Malformed OpenSCENARIO definitions | Stage 1 + Stage 2 |
| Logic Error | Flawed agent decision-making | Stage 2 |
| Integration Error | Component coordination failures | Stage 2 |
| Robustness Failure | Sensor noise/actuator delay handling | Stage 2 + Stage 3 |

---

## Research Questions

- **RQ1**: What defect categories exist in CARLA-based code?
- **RQ2**: Which techniques detect which categories? (overlap vs complementarity)
- **RQ3**: What are the computational costs and detection rates?
- **RQ4**: What evidence-based guidelines can guide validation budget allocation?

---

## Custom CARLA Rules Reference

| Rule ID | Severity | Issue |
|---------|----------|-------|
| CRL-001 | CRITICAL | Actor spawned without destroy() |
| CRL-002 | BLOCKER  | world.tick() inside sensor callback |
| CRL-003 | CRITICAL | Missing actor cleanup |
| CRL-004 | MAJOR    | set_attribute() called with no arguments |
| CRL-005 | MAJOR    | Sensor spawned with None transform |
| CRL-006 | MAJOR    | synchronous_mode enabled without teardown |

---

## Next Steps (Implementation Backlog)

- [ ] Implement `stage2_formal/run_model_checker.py` (VIVAS/nuXmv integration)
- [ ] Implement `stage2_formal/extract_agent_model.py` (agent → SMV model)
- [ ] Implement `stage3_llm/generate_scenarios.py` (GPT-4 generation loop)
- [ ] Implement `stage3_llm/validate_scenarios.py` (physical plausibility)
- [ ] Implement `defect_seeding/seed_defects.py` (automated defect injection)
- [ ] Select and fork 15–20 CARLA projects from GitHub
- [ ] Populate `defect_seeding/ground_truth.json` with real seeded defects
