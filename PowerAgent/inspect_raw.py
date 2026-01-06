
import os
import sys
from PyLTSpice import RawRead
import numpy as np

def inspect_raw(file_path):
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    print(f"Inspecting: {file_path}")
    try:
        ltr = RawRead(file_path)
        trace_names = ltr.get_trace_names()
        print(f"Traces: {trace_names}")

        target = next((t for t in trace_names if 'out' in t.lower() and 'v' in t.lower()), None)
        if target:
            print(f"Found voltage trace: {target}")
            steps = ltr.get_steps()
            print(f"Steps: {steps}")
            
            wave = ltr.get_trace(target).get_wave(steps[0])
            time = ltr.get_trace('time').get_wave(steps[0])
            
            print(f"Time points: {len(time)}")
            print(f"Voltage points: {len(wave)}")
            print(f"Time range: {np.min(time)} to {np.max(time)}")
            print(f"Voltage range: {np.min(wave)} to {np.max(wave)}")
            
            if np.isnan(wave).any():
                print("WARNING: Voltage trace contains NaNs!")
            if np.isinf(wave).any():
                print("WARNING: Voltage trace contains Infs!")
        else:
            print("No output voltage trace found (matching 'out' and 'v').")
            
    except Exception as e:
        print(f"Error reading raw file: {e}")

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    initial_raw = os.path.join(base_dir, "experiment_results", "initial_sim.raw")
    inspect_raw(initial_raw)
