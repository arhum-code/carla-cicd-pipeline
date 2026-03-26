#!/usr/bin/env python3
import json
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metrics', type=str, required=True)
    args = parser.parse_args()
    
    with open(args.metrics) as f:
        metrics = json.load(f)
    
    print("\n" + "="*60)
    print("PIPELINE METRICS SUMMARY")
    print("="*60)
    
    for stage in ['stage1', 'stage2', 'stage3']:
        if stage in metrics:
            print(f"\n{stage.upper()}:")
            print(f"  Detection rate: {metrics[stage].get('detection_rate', 0):.2%}")
            print(f"  Duration: {metrics[stage].get('duration_sec', 0)}s")
    
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    main()
