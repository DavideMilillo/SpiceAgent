"""
SpiceAgent Automated Benchmark Runner Utilities
==============================================
Provides headless, autonomous execution of SpiceAgent optimization loops
for reproducible experimental validation and paper data generation.
"""

import os
import sys
import time
import json
import numpy as np
import matplotlib.pyplot as plt
from typing import Dict, Any, List

# Ensure package in src can be imported
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from PyLTSpice import SimRunner, SpiceEditor, RawRead

class BenchmarkTracker:
    def __init__(self, experiment_name: str, results_dir: str):
        self.experiment_name = experiment_name
        self.results_dir = os.path.abspath(results_dir)
        os.makedirs(self.results_dir, exist_ok=True)
        self.trajectory: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.total_simulations = 0
        self.total_llm_calls = 0

    def log_step(self, iteration: int, parameters: Dict[str, Any], metrics: Dict[str, Any], notes: str = ""):
        entry = {
            "iteration": iteration,
            "timestamp": time.time() - self.start_time,
            "parameters": dict(parameters),
            "metrics": dict(metrics),
            "notes": notes
        }
        self.trajectory.append(entry)
        print(f"[{self.experiment_name}] Iteration {iteration}: Params={parameters} -> Metrics={metrics} {notes}")

    def save_summary(self) -> str:
        summary_path = os.path.join(self.results_dir, f"{self.experiment_name}_trajectory.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "experiment_name": self.experiment_name,
                "total_time_seconds": time.time() - self.start_time,
                "total_simulations": self.total_simulations,
                "total_llm_calls": self.total_llm_calls,
                "trajectory": self.trajectory
            }, f, indent=2)
        
        md_path = os.path.join(self.results_dir, f"{self.experiment_name}_table.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# Optimization Trajectory: {self.experiment_name}\n\n")
            f.write(f"- Total Wall-Clock Time: {time.time() - self.start_time:.1f} s\n")
            f.write(f"- Total SPICE Simulations: {self.total_simulations}\n")
            f.write(f"- Total LLM Iterations: {self.total_llm_calls}\n\n")
            
            if self.trajectory:
                first_params = list(self.trajectory[0]["parameters"].keys())
                first_metrics = list(self.trajectory[0]["metrics"].keys())
                headers = ["Iteration"] + first_params + first_metrics + ["Notes"]
                f.write("| " + " | ".join(headers) + " |\n")
                f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
                for entry in self.trajectory:
                    row = [str(entry["iteration"])]
                    for p in first_params:
                        row.append(str(entry["parameters"].get(p, "-")))
                    for m in first_metrics:
                        val = entry["metrics"].get(m, "-")
                        if isinstance(val, float):
                            row.append(f"{val:.4f}")
                        else:
                            row.append(str(val))
                    row.append(entry["notes"])
                    f.write("| " + " | ".join(row) + " |\n")
        
        print(f"[{self.experiment_name}] Summary saved to {summary_path} and {md_path}")
        return md_path
