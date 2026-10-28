"""
Task 2.3: Isolated Flyback Converter Optimization Benchmark
==========================================================
Executes an autonomous closed-loop optimization run on an isolated Flyback
converter with coupled inductors (K=0.98) and RCD snubber.

Directly resolves Reviewer 3's challenge:
"include zero-shot tests on more diverse topologies, such as flyback..."

Supports both Gemini and OpenAI providers.
Includes an automatic rate-limit safeguard (12s delay) to stay safely within free-tier RPM limits.
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

def run_flyback_optimization(provider: str = "gemini", model_name: str = None, max_iterations: int = 15):
    provider_tag = f"_{provider}"
    results_dir = os.path.join(script_dir, "results", f"flyback_converter{provider_tag}")
    os.makedirs(results_dir, exist_ok=True)
    tracker = BenchmarkTracker(f"Flyback_Optimization{provider_tag}", results_dir)
    
    work_dir = os.path.join(results_dir, "sim_workspace")
    os.makedirs(work_dir, exist_ok=True)
    
    circuit_src = os.path.join(repo_root, "Circuits", "Flyback_converter", "Flyback_converter.net")
    target_net = os.path.join(work_dir, "Flyback_converter.net")
    target_raw = os.path.join(work_dir, "Flyback_converter.raw")
    shutil.copy(circuit_src, target_net)
    
    # Initial state: Ton = 3.5u, Cout_val = 100u, Lp_val = 100u, Ls_val = 25u
    editor = SpiceEditor(target_net)
    editor.set_parameter("Ton", "3.5u")
    editor.set_parameter("Cout_val", "100u")
    editor.write_netlist(target_net)
    
    current_params = {"Ton": "3.5u", "Cout_val": "100u", "Lp_val": "100u", "Ls_val": "25u"}
    
    # Baseline simulation
    runner = SimRunner(output_folder=work_dir)
    runner.run(editor, run_filename="Flyback_converter.net")
    runner.wait_completion()
    tracker.total_simulations += 1
    
    # Evaluate baseline
    raw = RawRead(target_raw)
    steps = raw.get_steps()
    t_init = raw.get_trace("time").get_wave(steps[0])
    v_init = raw.get_trace("V(out)").get_wave(steps[0])
    idx = int(len(t_init) * 0.7)
    v_ss_init = v_init[idx:]
    init_metrics = {
        "V_mean": float(np.mean(v_ss_init)),
        "V_ripple_pp": float(np.ptp(v_ss_init)),
        "V_ripple_percent": float((np.ptp(v_ss_init) / np.mean(v_ss_init)) * 100)
    }
    tracker.log_step(0, current_params, init_metrics, f"Initial unoptimized Flyback (Model: {model_name or ('gemini-3.5-flash' if provider=='gemini' else 'gpt-4o')})")
    
    # Define tools for LLM
    @tool
    def update_circuit(changes: dict) -> str:
        """
        Updates parameters in the Flyback netlist.
        Args:
            changes: Dict of parameter names and new values.
                     Example: {'Ton': '5.2u', 'Cout_val': '220u'}
        """
        try:
            editor_cur = SpiceEditor(target_net)
            log = []
            for k, v in changes.items():
                v_clean = str(v).strip()
                editor_cur.set_parameter(k, v_clean)
                current_params[k] = v_clean
                log.append(f"Set .param {k} = {v_clean}")
            editor_cur.write_netlist(target_net)
            return "Updated Flyback netlist successfully:\n" + "\n".join(log)
        except Exception as e:
            return f"Error updating circuit: {e}"

    @tool
    def simulate_circuit() -> str:
        """Runs the LTSpice simulation with current Flyback netlist."""
        try:
            r = SimRunner(output_folder=work_dir)
            e = SpiceEditor(target_net)
            r.run(e, run_filename="Flyback_converter.net")
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
        Executes a Python snippet to extract steady-state metrics from target_raw.
        DO NOT import ltspice or RawRead. 'RawRead', 'np', and 'raw_path' are already provided!
        You must write results into the 'metrics' dict: metrics['V_mean'] and metrics['V_ripple_pp'].
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
        "You are an expert power electronics optimization agent.\n"
        "Your goal is to optimize an Isolated Flyback Converter (Vin = 24 V, transformer coupled with K=0.98, RCD snubber) "
        "to satisfy BOTH design specifications at the output node V(out):\n"
        "1. Regulated Output Voltage: V_mean = 12.0 V (+/- 0.3 V), i.e., [11.70 V, 12.30 V].\n"
        "2. Peak-to-Peak Voltage Ripple: V_ripple_pp < 0.10 V (which is < 0.83% ripple).\n\n"
        "Tunable parameters:\n"
        "- 'Ton': Primary MOSFET on-time pulse duration (period is 10us, i.e., 100 kHz). Initial is 3.5u. Increasing Ton increases energy transfer and raises V_mean.\n"
        "- 'Cout_val': Secondary output filtering capacitance (initial is 100u). Increasing Cout_val reduces V_ripple_pp.\n\n"
        "Optimization Loop:\n"
        "Step 1: Use `update_circuit` to adjust Ton and Cout_val based on error.\n"
        "Step 2: Use `simulate_circuit` to run LTSpice.\n"
        "Step 3: Use `evaluate_results` with Python code to compute metrics.\n"
        "  Example evaluation code:\n"
        "  LTR = RawRead(raw_path)\n"
        "  steps = LTR.get_steps()\n"
        "  t = LTR.get_trace('time').get_wave(steps[0])\n"
        "  v = LTR.get_trace('V(out)').get_wave(steps[0])\n"
        "  cut = int(len(t) * 0.7)\n"
        "  v_ss = v[cut:]\n"
        "  metrics['V_mean'] = float(np.mean(v_ss))\n"
        "  metrics['V_ripple_pp'] = float(np.ptp(v_ss))\n"
        "  metrics['V_ripple_percent'] = float((np.ptp(v_ss) / np.mean(v_ss)) * 100)\n"
        "Step 4: Check if specifications are met. When BOTH V_mean in [11.70, 12.30] V and V_ripple_pp < 0.10 V, conclude optimization."
    )
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"The initial Flyback simulation (Iter 0) yielded:\n"
            f"- V_mean = {init_metrics['V_mean']:.3f} V (Target: 12.00 +/- 0.30 V)\n"
            f"- V_ripple_pp = {init_metrics['V_ripple_pp']:.3f} V (Target: < 0.10 V)\n"
            f"Current parameters: {current_params}\n\n"
            f"Please propose the parameter updates and proceed with the optimization loop."
        ))
    ]
    
    current_metrics = dict(init_metrics)
    iteration = 1
    tool_map = {t.name: t for t in tools}
    
    print(f"\nStarting autonomous Flyback optimization ({provider} - {model_name or 'gemini-3.5-flash'})...")
    while iteration <= max_iterations:
        print(f"\n--- Iteration {iteration} ---")
        
        # Rate-limit safeguard for free tier RPM (12s delay)
        if provider == "gemini":
            print("[Rate-Limit Safeguard] Pausing 12s to stay safely below 5 RPM...")
            time.sleep(12)
            
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
                    tracker.log_step(iteration, current_params, current_metrics, f"Evaluation step ({provider} - {model_name or 'gemini-3.5-flash'})")
                except Exception as ex:
                    print(f"Error parsing metrics: {ex}")
                    
        # Check termination condition
        v_m = current_metrics.get("V_mean", 0.0)
        v_r = current_metrics.get("V_ripple_pp", 1.0)
        if 11.70 <= v_m <= 12.30 and v_r < 0.10:
            print("\n" + "="*50)
            print(f"SUCCESS! Target specifications ACHIEVED at iteration {iteration}:")
            print(f"  V_mean = {v_m:.4f} V (Target: 12.0 V +/- 0.3 V)")
            print(f"  V_ripple_pp = {v_r:.4f} V (Target: < 0.10 V)")
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
        v_fin = raw_final.get_trace("V(out)").get_wave(s_fin)
        
        plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=False)
        
        # Plot 1: Full Transient (Initial vs Final)
        ax1.plot(t_init * 1e3, v_init, 'r--', label=f"Initial (Ton=3.5µs, Cout=100µF): V_mean={init_metrics['V_mean']:.2f}V, Ripple={init_metrics['V_ripple_pp']:.3f}V", alpha=0.7)
        ax1.plot(t_fin * 1e3, v_fin, 'b-', label=f"Optimized [{provider}] (Ton={current_params.get('Ton')}, Cout={current_params.get('Cout_val')}): V_mean={current_metrics.get('V_mean', 0):.2f}V, Ripple={current_metrics.get('V_ripple_pp', 0):.3f}V", linewidth=1.5)
        ax1.axhline(12.0, color='g', linestyle=':', label="Target V_mean = 12.0 V")
        ax1.axhspan(11.7, 12.3, color='g', alpha=0.15, label="Tolerance Band (+/- 0.3 V)")
        ax1.set_title(f"Isolated Flyback Converter (K=0.98): Optimization via SpiceAgent [{provider.upper()}]", fontsize=13, fontweight='bold')
        ax1.set_ylabel("Output Voltage V(out) [V]", fontsize=11)
        ax1.legend(loc="lower right", frameon=True)
        ax1.grid(True, linestyle="--", alpha=0.5)
        
        # Plot 2: Steady-State Zoom (Last 0.5 ms)
        t_zoom_init = t_init[-int(len(t_init)*0.15):] * 1e3
        v_zoom_init = v_init[-int(len(v_init)*0.15):]
        t_zoom_fin = t_fin[-int(len(t_fin)*0.15):] * 1e3
        v_zoom_fin = v_fin[-int(len(v_fin)*0.15):]
        
        ax2.plot(t_zoom_init, v_zoom_init, 'r--', label=f"Initial Ripple: {init_metrics['V_ripple_pp']*1e3:.1f} mV ({init_metrics['V_ripple_percent']:.1f}%)", alpha=0.6)
        ax2.plot(t_zoom_fin, v_zoom_fin, 'b-', label=f"Optimized Ripple: {current_metrics.get('V_ripple_pp', 0)*1e3:.1f} mV ({current_metrics.get('V_ripple_percent', 0):.2f}%) [Target < 100 mV]", linewidth=1.8)
        ax2.set_title("Steady-State Output Ripple Zoom", fontsize=13, fontweight='bold')
        ax2.set_xlabel("Time [ms]", fontsize=11)
        ax2.set_ylabel("Output Voltage V(out) [V]", fontsize=11)
        ax2.legend(loc="upper right", frameon=True)
        ax2.grid(True, linestyle="--", alpha=0.5)
        
        plt.tight_layout()
        plot_path = os.path.join(results_dir, f"flyback_optimization_{provider}.png")
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
    
    run_flyback_optimization(provider=args.provider, model_name=args.model, max_iterations=args.max_iterations)
