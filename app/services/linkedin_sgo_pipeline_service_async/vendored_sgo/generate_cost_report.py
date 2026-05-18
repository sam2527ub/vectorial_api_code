#!/usr/bin/env python3
"""
Cost Estimation and Reporting Script for SGO Training

Generates a detailed cost report for all LLM API calls made during SGO training.
Can be run standalone to analyze cost data from a previous run, or integrated
into the main pipeline to generate reports after execution.
"""
import os
import sys
import json
import argparse
from pathlib import Path

# Set up package structure
scripts_dir = Path(__file__).parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))
import _package_setup

from sgo_training.utils.cost_tracker import (
    get_detailed_report,
    save_cost_report,
    print_cost_summary,
    MODEL_PRICING
)
from sgo_training.utils import io_utils
from sgo_training.config import settings


def generate_detailed_cost_report(output_file: str = None):
    """
    Generate a comprehensive cost report with detailed breakdown.
    
    Args:
        output_file: Optional path to save JSON report. If None, saves to default location.
    """
    report = get_detailed_report()
    
    if report["total_calls"] == 0:
        print("⚠️  No API calls tracked. Make sure cost tracking is enabled during execution.")
        return None
    
    # Generate detailed breakdown
    detailed_breakdown = {
        "report_metadata": {
            "total_calls": report["total_calls"],
            "grand_total_cost": report["grand_total_cost"],
            "report_timestamp": report["report_timestamp"],
            "pricing_used": MODEL_PRICING
        },
        "summary_by_model": {},
        "summary_by_call_type": {},
        "cost_breakdown": {
            "by_model": {},
            "by_call_type": {},
            "by_model_and_call_type": {}
        },
        "all_calls": report["all_calls"]
    }
    
    # Summary by model
    for model, data in report["summary"].items():
        detailed_breakdown["summary_by_model"][model] = {
            "total_calls": data["total_calls"],
            "total_input_tokens": data["total_input_tokens"],
            "total_output_tokens": data["total_output_tokens"],
            "total_cached_input_tokens": data["total_cached_input_tokens"],
            "total_cost": data["total_cost"],
            "avg_cost_per_call": data["total_cost"] / data["total_calls"] if data["total_calls"] > 0 else 0,
            "avg_input_tokens_per_call": data["total_input_tokens"] / data["total_calls"] if data["total_calls"] > 0 else 0,
            "avg_output_tokens_per_call": data["total_output_tokens"] / data["total_calls"] if data["total_calls"] > 0 else 0,
            "call_types": dict(data["by_call_type"])
        }
    
    # Summary by call type (across all models)
    call_type_summary = {}
    for model, data in report["summary"].items():
        for call_type, type_data in data["by_call_type"].items():
            if call_type not in call_type_summary:
                call_type_summary[call_type] = {
                    "total_calls": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cached_input_tokens": 0,
                    "total_cost": 0.0,
                    "models_used": set()
                }
            call_type_summary[call_type]["total_calls"] += type_data["calls"]
            call_type_summary[call_type]["total_input_tokens"] += type_data["input_tokens"]
            call_type_summary[call_type]["total_output_tokens"] += type_data["output_tokens"]
            call_type_summary[call_type]["total_cached_input_tokens"] += type_data["cached_input_tokens"]
            call_type_summary[call_type]["total_cost"] += type_data["cost"]
            call_type_summary[call_type]["models_used"].add(model)
    
    # Convert sets to lists for JSON serialization
    for call_type in call_type_summary:
        call_type_summary[call_type]["models_used"] = list(call_type_summary[call_type]["models_used"])
    
    detailed_breakdown["summary_by_call_type"] = call_type_summary
    
    # Cost breakdown by model and call type
    for model, data in report["summary"].items():
        detailed_breakdown["cost_breakdown"]["by_model_and_call_type"][model] = {}
        for call_type, type_data in data["by_call_type"].items():
            detailed_breakdown["cost_breakdown"]["by_model_and_call_type"][model][call_type] = {
                "calls": type_data["calls"],
                "input_tokens": type_data["input_tokens"],
                "output_tokens": type_data["output_tokens"],
                "cached_input_tokens": type_data["cached_input_tokens"],
                "cost": type_data["cost"],
                "avg_cost_per_call": type_data["cost"] / type_data["calls"] if type_data["calls"] > 0 else 0
            }
    
    # Save report
    if output_file is None:
        # Use default location in output directory
        output_file = os.path.join(
            settings.PATHS.get('OUTPUT_BASE_DIR', '.'),
            "cost_report.json"
        )
    
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(detailed_breakdown, f, indent=2)
    
    return detailed_breakdown


def print_detailed_cost_report(report: dict):
    """Print a formatted detailed cost report to console."""
    if not report:
        return
    
    metadata = report["report_metadata"]
    
    print("\n" + "="*100)
    print("DETAILED LLM API COST REPORT")
    print("="*100)
    print(f"Total API Calls: {metadata['total_calls']:,}")
    print(f"Grand Total Cost: ${metadata['grand_total_cost']:.4f}")
    print(f"Report Generated: {metadata['report_timestamp']}")
    
    # Summary by Model
    print("\n" + "="*100)
    print("COST BREAKDOWN BY MODEL")
    print("="*100)
    
    for model, data in sorted(report["summary_by_model"].items()):
        print(f"\n📊 Model: {model}")
        print(f"   Total Calls: {data['total_calls']:,}")
        print(f"   Total Input Tokens: {data['total_input_tokens']:,} ({data['total_input_tokens']/1_000_000:.3f}M)")
        print(f"   Total Output Tokens: {data['total_output_tokens']:,} ({data['total_output_tokens']/1_000_000:.3f}M)")
        if data['total_cached_input_tokens'] > 0:
            print(f"   Total Cached Input Tokens: {data['total_cached_input_tokens']:,} ({data['total_cached_input_tokens']/1_000_000:.3f}M)")
        print(f"   Total Cost: ${data['total_cost']:.4f}")
        print(f"   Average Cost per Call: ${data['avg_cost_per_call']:.6f}")
        print(f"   Average Input Tokens per Call: {data['avg_input_tokens_per_call']:.0f}")
        print(f"   Average Output Tokens per Call: {data['avg_output_tokens_per_call']:.0f}")
        
        # Breakdown by call type
        if data['call_types']:
            print(f"   Breakdown by Call Type:")
            for call_type, type_data in sorted(data['call_types'].items()):
                print(f"      • {call_type}:")
                print(f"          Calls: {type_data['calls']:,}")
                print(f"          Input: {type_data['input_tokens']:,} tokens")
                print(f"          Output: {type_data['output_tokens']:,} tokens")
                if type_data['cached_input_tokens'] > 0:
                    print(f"          Cached: {type_data['cached_input_tokens']:,} tokens")
                print(f"          Cost: ${type_data['cost']:.4f}")
                if type_data['calls'] > 0:
                    print(f"          Avg Cost per Call: ${type_data['cost']/type_data['calls']:.6f}")
    
    # Summary by Call Type (across all models)
    print("\n" + "="*100)
    print("COST BREAKDOWN BY CALL TYPE (Across All Models)")
    print("="*100)
    
    for call_type, data in sorted(report["summary_by_call_type"].items()):
        print(f"\n🔹 Call Type: {call_type}")
        print(f"   Total Calls: {data['total_calls']:,}")
        print(f"   Total Input Tokens: {data['total_input_tokens']:,} ({data['total_input_tokens']/1_000_000:.3f}M)")
        print(f"   Total Output Tokens: {data['total_output_tokens']:,} ({data['total_output_tokens']/1_000_000:.3f}M)")
        if data['total_cached_input_tokens'] > 0:
            print(f"   Total Cached Input Tokens: {data['total_cached_input_tokens']:,} ({data['total_cached_input_tokens']/1_000_000:.3f}M)")
        print(f"   Total Cost: ${data['total_cost']:.4f}")
        print(f"   Models Used: {', '.join(data['models_used'])}")
        if data['total_calls'] > 0:
            print(f"   Average Cost per Call: ${data['total_cost']/data['total_calls']:.6f}")
    
    # Detailed breakdown by model and call type
    print("\n" + "="*100)
    print("DETAILED BREAKDOWN BY MODEL AND CALL TYPE")
    print("="*100)
    
    for model in sorted(report["cost_breakdown"]["by_model_and_call_type"].keys()):
        print(f"\n📊 Model: {model}")
        for call_type, data in sorted(report["cost_breakdown"]["by_model_and_call_type"][model].items()):
            print(f"   • {call_type}:")
            print(f"      Calls: {data['calls']:,}")
            print(f"      Input: {data['input_tokens']:,} tokens | Output: {data['output_tokens']:,} tokens")
            if data['cached_input_tokens'] > 0:
                print(f"      Cached: {data['cached_input_tokens']:,} tokens")
            print(f"      Total Cost: ${data['cost']:.4f}")
            print(f"      Avg Cost per Call: ${data['avg_cost_per_call']:.6f}")
    
    print("\n" + "="*100)
    print(f"GRAND TOTAL COST: ${metadata['grand_total_cost']:.4f}")
    print("="*100 + "\n")


def main():
    """Main entry point for cost report generation."""
    parser = argparse.ArgumentParser(description='Generate detailed LLM API cost report for SGO training')
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output file path for JSON report (default: OUTPUT_BASE_DIR/cost_report.json)'
    )
    parser.add_argument(
        '--load-from',
        type=str,
        default=None,
        help='Load cost data from a previously saved JSON file instead of current tracking data'
    )
    parser.add_argument(
        '--print-summary',
        action='store_true',
        help='Print summary to console'
    )
    parser.add_argument(
        '--print-detailed',
        action='store_true',
        help='Print detailed report to console'
    )
    
    args = parser.parse_args()
    
    # If loading from file, load the data
    if args.load_from:
        if not os.path.exists(args.load_from):
            print(f"❌ Error: File not found: {args.load_from}")
            return
        
        with open(args.load_from, 'r') as f:
            saved_data = json.load(f)
        
        # Reconstruct cost data from saved file
        # (This would require modifying cost_tracker to support loading)
        print(f"⚠️  Loading from file not yet implemented. Use current tracking data instead.")
        return
    
    # Generate report from current tracking data
    report = generate_detailed_cost_report(args.output)
    
    if report:
        if args.print_summary:
            print_cost_summary()
        
        if args.print_detailed:
            print_detailed_cost_report(report)
        
        if args.output:
            print(f"✅ Cost report saved to: {args.output}")
        else:
            default_output = os.path.join(
                settings.PATHS.get('OUTPUT_BASE_DIR', '.'),
                "cost_report.json"
            )
            print(f"✅ Cost report saved to: {default_output}")
    else:
        print("⚠️  No cost data available. Make sure the SGO training pipeline has been run with cost tracking enabled.")


if __name__ == "__main__":
    main()
















