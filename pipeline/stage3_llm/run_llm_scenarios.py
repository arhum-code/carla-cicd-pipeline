#!/usr/bin/env python3
"""
Stage 3: Execute LLM-generated scenarios in CARLA
Reuses Stage 2 execution engine (run_scenario, log scraping, failure detection).
Produces results/stage3/simulation_results.json compatible with aggregate_pipeline_metrics.py
"""
import sys
import json
import time
import argparse
import os
from pathlib import Path
from dataclasses import asdict

# ── Import Stage 2 execution engine ──────────────────────────────────────────
# Adds stage2_formal to path so we can reuse run_scenario() directly
_STAGE2_DIR = Path(__file__).parent.parent / 'stage2_formal'
sys.path.insert(0, str(_STAGE2_DIR))

try:
    from run_scenarios import run_scenario, scrape_log_for_failures, is_confirmed_detection
    print("✓ Stage 2 execution engine loaded")
except ImportError as e:
    print(f"✗ Could not import Stage 2 runner: {e}")
    print(f"  Expected location: {_STAGE2_DIR / 'run_scenarios.py'}")
    sys.exit(1)


def execute_llm_scenarios(
    scenarios_dir: Path,
    scenario_runner: Path,
    output_file: Path,
    carla_port: int = 2000,
    timeout: int = 30,
    stage3_metadata: Path = None,
) -> dict:
    """
    Run all .xosc files in scenarios_dir through CARLA ScenarioRunner.
    Produces output JSON compatible with aggregate_pipeline_metrics.py.
    """

    # ── Find all generated scenarios ─────────────────────────────────────────
    xosc_files = sorted(scenarios_dir.glob('*.xosc'))
    if not xosc_files:
        print(f"✗ No .xosc files found in {scenarios_dir}")
        return {}

    print(f"\nStage 3 — LLM Scenario Execution")
    print(f"{'='*55}")
    print(f"Scenarios to run  : {len(xosc_files)}")
    print(f"ScenarioRunner    : {scenario_runner}")
    print(f"CARLA port        : {carla_port}")
    print(f"Per-scenario limit: {timeout}s")

    # ── Load Stage 3 metadata if available (for requirement mapping) ─────────
    requirement_map = {}
    if stage3_metadata and stage3_metadata.exists():
        try:
            meta = json.loads(stage3_metadata.read_text())
            for s in meta.get('scenarios', []):
                requirement_map[s['filename']] = s.get('requirement', 'unknown')
            print(f"Metadata loaded   : {len(requirement_map)} scenario requirements")
        except Exception as e:
            print(f"Warning: Could not load metadata: {e}")

    # ── Verify CARLA is reachable before starting ────────────────────────────
    print(f"{'='*55}\n")
    try:
        import carla
        client = carla.Client('localhost', carla_port)
        client.set_timeout(10.0)
        version = client.get_server_version()
        print(f"✓ CARLA server connected: v{version}\n")
    except Exception as e:
        print(f"⚠️  Cannot connect to CARLA: {e}")
        print("   Make sure CARLA is running before executing scenarios.\n")

    # ── Run each scenario ─────────────────────────────────────────────────────
    results = []
    confirmed = 0

    for i, xosc_file in enumerate(xosc_files, 1):
        print(f"[{i}/{len(xosc_files)}] {xosc_file.name}")

        # Attach the safety requirement to the result for richer reporting
        requirement = requirement_map.get(xosc_file.name, 'LLM-generated scenario')
        print(f"  Requirement: {requirement}")

        result = run_scenario(
            scenario_file=str(xosc_file),
            scenario_runner_py=str(scenario_runner),
            carla_port=carla_port,
            timeout=timeout,
        )

        # Tag with Stage 3 metadata
        result_dict = asdict(result)
        result_dict['safety_requirement'] = requirement
        result_dict['stage'] = 'stage3_llm'
        results.append(result_dict)

        if result.confirmed_detection:
            confirmed += 1

        # Brief pause between scenarios so CARLA can reset cleanly
        if i < len(xosc_files):
            time.sleep(3)

    # ── Summary ───────────────────────────────────────────────────────────────
    ran = sum(1 for r in results if r['ran'])
    detection_rate = round(confirmed / ran * 100, 1) if ran > 0 else 0.0

    print(f"\n{'='*55}")
    print(f"STAGE 3 SIMULATION RESULTS")
    print(f"{'='*55}")
    print(f"Scenarios run        : {ran}/{len(results)}")
    print(f"Confirmed detections : {confirmed}/{ran} ({detection_rate}%)")
    print()
    for r in results:
        status = "✅" if r['confirmed_detection'] else ("⚠️ " if r.get('error') else "❌")
        name   = Path(r['scenario_file']).stem
        reason = r['detection_reason']
        print(f"  {status} {name:<35} {reason}")

    # ── Save output (matches format expected by aggregate_pipeline_metrics.py) 
    output = {
        'stage': 'stage3_llm',
        'scenarios_dir': str(scenarios_dir),
        'total_scenarios': len(results),
        'scenarios_ran': ran,
        'confirmed_detections': confirmed,
        'detection_rate_pct': detection_rate,
        'results': results,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(output, indent=2))
    print(f"\n✓ Results saved to: {output_file}")

    return output


def main():
    parser = argparse.ArgumentParser(
        description='Stage 3: Run LLM-generated scenarios in CARLA'
    )
    parser.add_argument('--scenarios-dir',    type=Path, required=True,
                        help='Directory containing .xosc files from llm_hf.py')
    parser.add_argument('--scenario-runner',  type=Path, required=True,
                        help='Path to scenario_runner.py')
    parser.add_argument('--output',           type=Path, required=True,
                        help='Output JSON path (e.g. results/stage3/simulation_results.json)')
    parser.add_argument('--carla-port',       type=int, default=2000)
    parser.add_argument('--timeout',          type=int, default=30,
                        help='Per-scenario timeout in seconds')
    parser.add_argument('--stage3-metadata',  type=Path, default=None,
                        help='Optional: metadata JSON from llm_hf.py for requirement mapping')

    args = parser.parse_args()

    # Validate inputs
    if not args.scenarios_dir.exists():
        print(f"✗ Scenarios directory not found: {args.scenarios_dir}")
        return 1
    if not args.scenario_runner.exists():
        print(f"✗ scenario_runner.py not found: {args.scenario_runner}")
        return 1

    result = execute_llm_scenarios(
        scenarios_dir=args.scenarios_dir,
        scenario_runner=args.scenario_runner,
        output_file=args.output,
        carla_port=args.carla_port,
        timeout=args.timeout,
        stage3_metadata=args.stage3_metadata,
    )

    return 0 if result else 1


if __name__ == '__main__':
    exit(main())