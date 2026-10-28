"""
Task 2.2: Precision OpAmp Bridge Amplifier Optimization Benchmark
================================================================
Executes an autonomous closed-loop optimization run using SpiceAgent
to resolve Reviewer 3's criticism: ensuring the agent completes the OpAmp
case study end-to-end to reach V(n006)_pp = 1.30 V +/- 0.05 V.

Supports both OpenAI (gpt-4o) and Gemini (gemini-3.5-flash) providers.
"""

import os
import sys
import shutil
import time
import json
import traceback
import argparse
import numpy as np
import matplotlib.pyplot as plt

# Auto-load keys from registry if missing from os.environ
if sys.platform == "win32":
    import winreg
    for k in ['GOOGLE_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY']:
        if k not in os.environ or not os.environ[k]:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as reg_key:
                    val, _ = winreg.QueryValueEx(reg_key, k)
                    os.environ[k] = val
            except Exception:
                pass

script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(script_dir, ".."))
sys.path.insert(0, os.path.join(repo_root, "src"))
sys.path.insert(0, script_dir)

from runner_utils import BenchmarkTracker
from PyLTSpice import SimRunner, SpiceEditor, RawRead
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

def get_llm(provider: str, model_name: str = None):
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        m_name = model_name or "gemini-3.5-flash"
        print(f"Using Gemini Provider with model: {m_name}")
        return ChatGoogleGenerativeAI(model=m_name, temperature=0.1)
    else:
        from langchain_openai import ChatOpenAI
        m_name = model_name or "gpt-4o"
        print(f"Using OpenAI Provider with model: {m_name}")
        return ChatOpenAI(model=m_name, temperature=0.1)

def run_opamp_optimization(provider: str = "gemini", model_name: str = None, max_iterations: int = 15):
    provider_tag = f"_{provider}"
    results_dir = os.path.join(script_dir, "results", f"opamp_bridge{provider_tag}")
    os.makedirs(results_dir, exist_ok=True)
    tracker = BenchmarkTracker(f"OpAmp_Bridge_Optimization{provider_tag}", results_dir)
    
    work_dir = os.path.join(results_dir, "sim_workspace")
    os.makedirs(work_dir, exist_ok=True)
    
    circuit_src = os.path.join(repo_root, "Circuits", "OpAmps", "LT1057.asc")
    target_asc = os.path.join(work_dir, "LT1057.asc")
    target_net = os.path.join(work_dir, "LT1057.net")
    target_raw = os.path.join(work_dir, "LT1057.raw")
    shutil.copy(circuit_src, target_asc)
    
    # Initialize initial state: R3 = 28k (as reported in reviewer comment)
    editor = SpiceEditor(target_asc)
    editor.set_component_value("R3", "28k")
    editor.write_netlist(target_net)
    
    current_params = {"R3": "28k"}
    
    # Baseline simulation
    runner = SimRunner(output_folder=work_dir)
    runner.run(editor, run_filename="LT1057.net")
    runner.wait_completion()
    tracker.total_simulations += 1
    
    # Evaluate baseline
    raw = RawRead(target_raw)
    steps = raw.get_steps()
    t_init = raw.get_trace("time").get_wave(steps[0])
    v_init = raw.get_trace("V(n006)").get_wave(steps[0])
    idx = int(len(t_init) * 0.7)
    v_ss_init = v_init[idx:]
    init_metrics = {
        "V_mean": float(np.mean(v_ss_init)),
        "V_pp": float(np.ptp(v_ss_init))
    }
    tracker.log_step(0, current_params, init_metrics, "Initial unoptimized configuration (R3=28k)")
    
    # Define tools for LLM
    @tool
    def update_circuit(changes: dict) -> str:
        """
        Updates components in the OpAmp netlist.
        Args:
            changes: Dict of component names and their new values. Example: {'R3': '22k'}
        """
        try:
            editor_cur = SpiceEditor(target_net)
            log = []
            for k, v in changes.items():
                v_clean = str(v).strip()
                editor_cur.set_component_value(k, v_clean)
                current_params[k] = v_clean
                log.append(f"Set component {k} = {v_clean}")
            editor_cur.write_netlist(target_net)
            return "Updated netlist successfully:\n" + "\n".join(log)
        except Exception as e:
            return f"Error updating circuit: {e}"

    @tool
    def simulate_circuit() -> str:
        """Runs the LTSpice simulation with current netlist."""
        try:
            r = SimRunner(output_folder=work_dir)
            e = SpiceEditor(target_net)
            r.run(e, run_filename="LT1057.net")
            r.wait_completion()
            tracker.total_simulations += 1
            if os.path.exists(target_raw) and os.path.getsize(target_raw) > 0:
                return "Simulation completed successfully. RAW waveform updated."
            else:
                return "Simulation ran but RAW file was empty or failed."
        except Exception as e:
            return f"Error during simulation: {e}"

    @tool
    def evaluate_results(python_script: str) -> str:
        """
        Executes a Python snippet to extract steady-state metrics for node V(n006).
        DO NOT import ltspice or RawRead. 'RawRead', 'np', and 'raw_path' are already provided!
        You must write results into the 'metrics' dict: metrics['V_mean'] and metrics['V_pp'].
        """
        try:
            clean_script = python_script.replace("```python", "").replace("```", "").strip()
            local_scope = {"raw_path": target_raw, "RawRead": RawRead, "np": np, "metrics": {}}
            exec(clean_script, globals(), local_scope)
            m = local_scope.get("metrics", {})
            if m:
                return f"Calculated Metrics: {m}"
            return "Script ran but 'metrics' dictionary was empty."
        except Exception as e:
            return f"Error executing evaluation script:\n{traceback.format_exc()}"

    tools = [update_circuit, simulate_circuit, evaluate_results]
    model = get_llm(provider, model_name)
    model = model.bind_tools(tools)
    
    # System Prompt
    system_prompt = (
        "You are an expert analog electronics optimization agent.\n"
        "Your goal is to tune the Fast, Precision Bridge Amplifier (LT1057 dual JFET + LT1010 buffers) "
        "to satisfy the target specification at node V(n006):\n"
        "- Peak-to-Peak Output Voltage Swing: V_pp = 1.30 V (+/- 0.05 V), i.e., [1.25 V, 1.35 V].\n\n"
        "Tunable parameter:\n"
        "- 'R3': Feedback resistor (initial value is '28k'). Reducing R3 reduces the gain and V_pp swing.\n\n"
        "Optimization Loop:\n"
        "Step 1: Use `update_circuit` to adjust R3 based on the error between measured V_pp and target 1.30 V.\n"
        "Step 2: Use `simulate_circuit` to run LTSpice.\n"
        "Step 3: Use `evaluate_results` with Python code to compute metrics.\n"
        "  Example evaluation code:\n"
        "  LTR = RawRead(raw_path)\n"
        "  steps = LTR.get_steps()\n"
        "  t = LTR.get_trace('time').get_wave(steps[0])\n"
        "  v = LTR.get_trace('V(n006)').get_wave(steps[0])\n"
        "  cut = int(len(t) * 0.7)\n"
        "  v_ss = v[cut:]\n"
        "  metrics['V_mean'] = float(np.mean(v_ss))\n"
        "  metrics['V_pp'] = float(np.ptp(v_ss))\n"
        "Step 4: Check if specifications are met. When V_pp is in [1.25, 1.35] V, conclude optimization."
    )
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"The initial simulation (Iter 0) with R3 = 28k yielded:\n"
            f"- V_pp = {init_metrics['V_pp']:.3f} V (Target: 1.30 +/- 0.05 V)\n"
            f"- V_mean = {init_metrics['V_mean']:.3f} V\n"
            f"Current parameters: {current_params}\n\n"
            f"Please propose the parameter updates and proceed with the optimization loop."
        ))
    ]
    
    current_metrics = dict(init_metrics)
    iteration = 1
    tool_map = {t.name: t for t in tools}
    
    print(f"\nStarting autonomous closed-loop optimization ({provider})...")
    while iteration <= max_iterations:
        print(f"\n--- Iteration {iteration} ---")
        tracker.total_llm_calls += 1
        ai_msg = model.invoke(messages)
        messages.append(ai_msg)
        
        if ai_msg.content:
            print(f"Agent Reasoning: {ai_msg.content[:200]}...")
            
        if not ai_msg.tool_calls:
            print("Agent stopped making tool calls.")
            break
            
        for tool_call in ai_msg.tool_calls:
            t_name = tool_call["name"]
            t_args = tool_call["args"]
            print(f"Tool Call: {t_name}({t_args})")
            
            tool_fn = tool_map[t_name]
            tool_output = tool_fn.invoke(t_args)
            messages.append(ToolMessage(content=str(tool_output), tool_call_id=tool_call["id"]))
            
            if t_name == "evaluate_results" and "Calculated Metrics:" in tool_output:
                try:
                    metrics_str = tool_output.split("Calculated Metrics:")[1].strip()
                    m_eval = eval(metrics_str)
                    current_metrics = m_eval
                    tracker.log_step(iteration, current_params, current_metrics, f"Evaluation step ({provider})")
                except Exception as ex:
                    print(f"Error parsing metrics: {ex}")
                    
        # Check termination condition
        v_p = current_metrics.get("V_pp", 2.0)
        if 1.25 <= v_p <= 1.35:
            print("\n" + "="*50)
            print(f"SUCCESS! Target specifications ACHIEVED at iteration {iteration}:")
            print(f"  V_pp = {v_p:.4f} V (Target: 1.30 V +/- 0.05 V)")
            print(f"  Final Parameters: {current_params}")
            print("="*50)
            break
            
        iteration += 1

    # Save summary
    tracker.save_summary()
    
    # Generate Publication-Quality Comparison Figure
    try:
        raw_final = RawRead(target_raw)
        s_fin = raw_final.get_steps()[0]
        t_fin = raw_final.get_trace("time").get_wave(s_fin)
        v_fin = raw_final.get_trace("V(n006)").get_wave(s_fin)
        
        plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # Steady-state window (last 1 ms of 3 ms transient)
        mask_init = t_init >= 2e-3
        mask_fin = t_fin >= 2e-3
        
        ax.plot(t_init[mask_init] * 1e3, v_init[mask_init], 'r--', label=f"Initial (R3=28kΩ): V_pp = {init_metrics['V_pp']:.3f} V", alpha=0.7)
        ax.plot(t_fin[mask_fin] * 1e3, v_fin[mask_fin], 'b-', label=f"Optimized [{provider}] (R3={current_params.get('R3')}): V_pp = {current_metrics.get('V_pp', 0):.3f} V [Target: 1.30 ± 0.05 V]", linewidth=1.8)
        
        ax.set_title(f"Precision OpAmp Bridge Amplifier: Closed-Loop Gain Tuning [{provider.upper()}]", fontsize=13, fontweight='bold')
        ax.set_xlabel("Time [ms]", fontsize=11)
        ax.set_ylabel("Output Voltage V(n006) [V]", fontsize=11)
        ax.legend(loc="upper right", frameon=True)
        ax.grid(True, linestyle="--", alpha=0.5)
        
        plt.tight_layout()
        plot_path = os.path.join(results_dir, f"opamp_optimization_{provider}.png")
        plt.savefig(plot_path, dpi=300)
        plt.close()
        print(f"Publication-quality figure saved to: {plot_path}")
    except Exception as ex:
        print(f"Error creating plot: {ex}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["gemini", "openai"], default="gemini")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max_iterations", type=int, default=15)
    args = parser.parse_args()
    
    run_opamp_optimization(provider=args.provider, model_name=args.model, max_iterations=args.max_iterations)
