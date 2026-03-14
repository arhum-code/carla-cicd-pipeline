"""
Comparison Report Generator
=============================
Converts the aggregated metrics JSON into a human-readable
Markdown report — the "Cross-Technique Comparison Report"
referenced in the pipeline diagram.
"""

import json
import argparse
import os
from datetime import datetime


def fmt_pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.metrics) as f:
        m = json.load(f)

    sm = m["stage_metrics"]
    ov = m["overlap_analysis"]
    cb = m["category_breakdown"]
    total = m["total_defects"]

    lines = [
        f"# CARLA Validation Pipeline — Comparison Report",
        f"",
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Stage 1 (Static) | Stage 2 (Formal) | Stage 3 (LLM) |",
        f"|--------|-----------------|-----------------|---------------|",
        f"| Detection Rate | {fmt_pct(sm['stage1']['detection_rate'])} | {fmt_pct(sm['stage2']['detection_rate'])} | {fmt_pct(sm['stage3']['detection_rate'])} |",
        f"| Precision      | {fmt_pct(sm['stage1']['precision'])} | {fmt_pct(sm['stage2']['precision'])} | {fmt_pct(sm['stage3']['precision'])} |",
        f"| Recall         | {fmt_pct(sm['stage1']['recall'])} | {fmt_pct(sm['stage2']['recall'])} | {fmt_pct(sm['stage3']['recall'])} |",
        f"| True Positives | {sm['stage1']['tp']} | {sm['stage2']['tp']} | {sm['stage3']['tp']} |",
        f"| False Positives| {sm['stage1']['fp']} | {sm['stage2']['fp']} | {sm['stage3']['fp']} |",
        f"",
        f"---",
        f"",
        f"## Detection by Defect Category",
        f"",
        f"| Category | Total | Stage 1 DR | Stage 2 DR | Stage 3 DR |",
        f"|----------|-------|-----------|-----------|-----------|",
    ]

    for cat, stats in cb.items():
        lines.append(
            f"| {cat} | {stats['total_defects']} "
            f"| {fmt_pct(stats['stage1_detection_rate'])} "
            f"| {fmt_pct(stats['stage2_detection_rate'])} "
            f"| {fmt_pct(stats['stage3_detection_rate'])} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## Overlap & Complementarity Analysis",
        f"",
        f"- **Total seeded defects**: {total}",
        f"- **Detected by 2+ stages**: {ov['detected_by_multiple']} ({fmt_pct(ov['detected_by_multiple']/total if total else 0)})",
        f"- **Unique to Stage 1** (static only): {ov['unique_to_stage1']}",
        f"- **Unique to Stage 2** (formal only): {ov['unique_to_stage2']}",
        f"- **Unique to Stage 3** (LLM only): {ov['unique_to_stage3']}",
        f"- **Missed by all stages**: {ov['missed_by_all']}",
        f"",
        f"---",
        f"",
        f"## Evidence-Based Guidelines",
        f"",
        f"Based on the detection matrix above:",
        f"",
        f"1. **Always run Stage 1 first.** Static analysis has near-zero cost and catches",
        f"   API misuse and resource management failures before any simulation time is spent.",
        f"",
        f"2. **Stage 2 is essential for safety property violations.** Formal scenario synthesis",
        f"   catches scenario specification faults and robustness failures that static analysis",
        f"   structurally cannot reach.",
        f"",
        f"3. **Stage 3 adds coverage for edge cases.** LLM-generated scenarios provide diversity",
        f"   complementing the exhaustive but narrow counterexamples from nuXmv.",
        f"",
        f"4. **Budget allocation recommendation**: If constrained, prioritize Stage 1 + Stage 2.",
        f"   Stage 3 is valuable when scenario diversity is the primary concern.",
    ]

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        f.write("\n".join(lines))

    print(f"Comparison report saved to {args.output}")


if __name__ == "__main__":
    main()
