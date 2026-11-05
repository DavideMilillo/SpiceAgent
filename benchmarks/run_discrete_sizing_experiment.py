#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Discrete Component Sizing Benchmark (Stage 3 in SpiceAgent)
==========================================================
Bridges the gap between continuous-parameter optimization and practical
hardware realization by mapping continuous values to commercial discrete
components (E12/E24 series) from `components_list.csv` and performing
active closed-loop compensation.

Target Specifications:
- Output Voltage: V_mean = 5.00 V +/- 0.05 V (strict 1% tolerance)
- Output Ripple:  V_ripple_pp < 0.05 V (< 1%)
- Components: Strictly discrete commercial parts from catalog
"""

import os
import sys
import csv
import time
import json
import shutil
import argparse
import traceback
import numpy as np
import matplotlib.pyplot as plt

# Auto-load keys from registry if available
if sys.platform == "win32":
    import winreg
    for k in ['GOOGLE_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY']:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, k)
                if val:
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

# Locate components database
COMPONENTS_DB = os.path.join(repo_root, "src", "spiceagent", "resources", "components_list.csv")
if not os.path.exists(COMPONENTS_DB):
    COMPONENTS_DB = os.path.join(repo_root, "PowerAgent", "components_list.csv")

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

def run_discrete_sizing(provider: str = "openai", model_name: str = None, max_iterations: int = 12):
    provider_tag = f"_{provider}"
    results_dir = os.path.join(script_dir, "results", f"discrete_sizing{provider_tag}")
    os.makedirs(results_dir, exist_ok=True)
    tracker = BenchmarkTracker(f"Discrete_Sizing{provider_tag}", results_dir)
    
    work_dir = os.path.join(results_dir, "sim_workspace")
    os.makedirs(work_dir, exist_ok=True)
    
    circuit_src = os.path.join(repo_root, "Circuits", "Buck_converter", "Buck_converter_real.asc")
    target_asc = os.path.join(work_dir, "Buck_converter_real.asc")
    target_net = os.path.join(work_dir, "Buck_converter_real.net")
    target_raw = os.path.join(work_dir, "Buck_converter_real.raw")
    shutil.copy(circuit_src, target_asc)
    
    # -------------------------------------------------------------
    # Step 0: Initialize Continuous Baseline (from Stage 2 result)
    # Continuous ideal design: L_nom = 14u, C_nom = 135u, Ton = 3.80u
    # -------------------------------------------------------------
    editor = SpiceEditor(target_asc)
    editor.set_component_value("Vsw", "PULSE(0 10 0 1n 1n 3.80u 10u)")
    editor.set_parameter("C_nom", "135u")
    editor.set_parameter("L_nom", "14u")
    editor.write_netlist(target_net)
    
    current_params = {
        "Ton": "3.80u",
        "C_nom": "135u",
        "L_nom": "14u",
        "L_type": "continuous_ideal",
        "C_type": "continuous_ideal"
    }
    
    # Baseline simulation
    runner = SimRunner(output_folder=work_dir)
    runner.run(editor, run_filename="Buck_converter_real.net")
    runner.wait_completion()
    tracker.total_simulations += 1
    
    raw_cont_path = os.path.join(results_dir, "baseline_continuous.raw")
    if os.path.exists(target_raw):
        shutil.copy(target_raw, raw_cont_path)
        
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
    tracker.log_step(0, current_params, init_metrics, f"Continuous Ideal Baseline (Non-standard parts: L=14u, C=135u)")
    
    raw_quantized_path = os.path.join(results_dir, "discrete_quantized.raw")
    has_saved_quantized = False
    
    # -------------------------------------------------------------
    # Define Tools for Discrete Sizing Agent
    # -------------------------------------------------------------
    @tool
    def search_components_db(component_type: str) -> str:
        """
        Searches the components_list.csv database for commercially available discrete components.
        Args:
            component_type: Type of component, e.g., 'C' (capacitors), 'L' (inductors), 'R' (resistors).
        Returns:
            Table of commercial components with Value, ESR (Ohms), Package, and Notes.
        """
        if not os.path.exists(COMPONENTS_DB):
            return "Error: components_list.csv database not found."
        try:
            results = []
            with open(COMPONENTS_DB, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('Component_Type', '').strip().upper() == component_type.strip().upper():
                        results.append(row)
            if results:
                formatted = [f"Value: {r['Value']:>6s} | ESR: {r['ESR']} Ohm | Package: {r['Package']:<6s} | Notes: {r['Notes']}" for r in results]
                return f"Available discrete components for type '{component_type}':\n" + "\n".join(formatted)
            return f"No components found for type '{component_type}'."
        except Exception as e:
            return f"Error reading database: {e}"

    @tool
    def update_circuit(changes: dict) -> str:
        """
        Updates components or parameters in the netlist.
        Args:
            changes: Dict of parameter names and new values.
                     Example: {'L_nom': '15u', 'C_nom': '150u', 'Ton': '3.78u'}
        """
        try:
            editor_cur = SpiceEditor(target_net)
            log = []
            for k, v in changes.items():
                v_clean = str(v).strip()
                if k == "Ton":
                    editor_cur.set_component_value("Vsw", f"PULSE(0 10 0 1n 1n {v_clean} 10u)")
                    current_params["Ton"] = v_clean
                    log.append(f"Set Vsw Ton = {v_clean}")
                elif k in ["C_nom", "L_nom", "C_min", "I_sat", "V_coeff"]:
                    editor_cur.set_parameter(k, v_clean)
                    current_params[k] = v_clean
                    log.append(f"Set .param {k} = {v_clean}")
            editor_cur.write_netlist(target_net)
            return f"Circuit parameters updated successfully:\n" + "\n".join(log)
        except Exception as e:
            return f"Error updating circuit: {e}"

    @tool
    def simulate_circuit() -> str:
        """
        Executes the LTSpice transient simulation on the updated circuit netlist.
        """
        nonlocal has_saved_quantized
        try:
            runner_sim = SimRunner(output_folder=work_dir)
            editor_sim = SpiceEditor(target_net)
            runner_sim.run(editor_sim, run_filename="Buck_converter_real.net")
            runner_sim.wait_completion()
            tracker.total_simulations += 1
            
            if os.path.exists(target_raw) and os.path.getsize(target_raw) > 0:
                # Save the first discrete uncompensated run for waveform comparison
                if not has_saved_quantized and current_params.get("L_nom") in ["10u", "15u", "22u"] and current_params.get("C_nom") in ["100u", "120u", "150u", "180u", "220u", "330u"]:
                    shutil.copy(target_raw, raw_quantized_path)
                    has_saved_quantized = True
                return f"Simulation completed successfully. Output RAW: Buck_converter_real.raw ({os.path.getsize(target_raw)} bytes)"
            else:
                return "Simulation finished but RAW file was not generated or empty."
        except Exception as e:
            return f"Simulation error: {e}"

    @tool
    def evaluate_results(python_script: str) -> str:
        """
        Executes a Python snippet to extract steady-state metrics from target_raw.
        DO NOT import ltspice. 'RawRead', 'np', and 'raw_path' are ALREADY provided!
        You must write results into the 'metrics' dict: metrics['V_mean'] and metrics['V_ripple_pp'].
        Example:
            LTR = RawRead(raw_path)
            steps = LTR.get_steps()
            t = LTR.get_trace('time').get_wave(steps[0])
            v = LTR.get_trace('V(out)').get_wave(steps[0])
            cut = int(len(t) * 0.7)
            v_ss = v[cut:]
            metrics['V_mean'] = float(np.mean(v_ss))
            metrics['V_ripple_pp'] = float(np.ptp(v_ss))
            metrics['V_ripple_percent'] = float((np.ptp(v_ss) / np.mean(v_ss)) * 100)
        """
        clean_script = python_script.replace("```python", "").replace("```", "").strip()
        # Clean hallucinated ltspice imports gracefully
        cleaned_lines = []
        for line in clean_script.splitlines():
            if "ltspice" in line:
                continue
            cleaned_lines.append(line)
        clean_script = "\n".join(cleaned_lines)
        
        try:
            local_scope = {
                'raw_path': target_raw,
                'RawRead': RawRead,
                'np': np,
                'metrics': {}
            }
            exec(clean_script, globals(), local_scope)
            metrics_res = local_scope.get('metrics', {})
            if metrics_res:
                return f"Calculated Metrics: {metrics_res}"
            return "Script executed successfully but 'metrics' dictionary was empty. Please assign metrics['V_mean'] and metrics['V_ripple_pp']."
        except Exception as e:
            return f"Execution error: {e}\n{traceback.format_exc()}"

    tools = [search_components_db, update_circuit, simulate_circuit, evaluate_results]
    model = get_llm(provider, model_name).bind_tools(tools)
    
    # -------------------------------------------------------------
    # Sizer System Prompt
    # -------------------------------------------------------------
    system_prompt = (
        "You are the 'Discrete Sizing Agent' in the SpiceAgent multi-agent framework.\n"
        "Your mission is to transition the circuit from an unconstrained, continuous-variable design "
        "to commercially available, discrete standard components (E12/E24 series) while preserving "
        "stringent performance specifications.\n\n"
        "Design Specifications:\n"
        "- Target Output Voltage: V_mean = 5.00 V +/- 0.05 V (strict 1% tolerance window [4.95 V, 5.05 V])\n"
        "- Target Peak-to-Peak Ripple: V_ripple_pp < 0.05 V (< 1%)\n"
        "- Component Constraints: ALL inductors (L_nom) and capacitors (C_nom) must strictly be "
        "selected from the commercial catalog in `components_list.csv`.\n\n"
        "Current Initial State (Continuous Stage 2 Output):\n"
        "- Inductor L1: non-standard continuous value L_nom = 14u\n"
        "- Output Capacitor Cout: non-standard continuous value C_nom = 135u\n"
        "- Pulse Width: Ton = 3.80u (Switching period T = 10u, Fsw = 100 kHz)\n"
        f"- Current Performance: V_mean = {init_metrics['V_mean']:.4f} V, Ripple = {init_metrics['V_ripple_pp']:.4f} V\n\n"
        "Systematic Methodology:\n"
        "1. Query Catalog: Call `search_components_db('L')` and `search_components_db('C')`.\n"
        "2. Select Nearest Discrete Parts: Choose standard discrete commercial parts (e.g. L=15u or 10u, C=120u or 150u).\n"
        "3. Apply and Simulate: Update the circuit using `update_circuit` and execute `simulate_circuit`.\n"
        "4. Evaluate Quantization Impact: Use `evaluate_results` to quantify the shift in V_mean and ripple caused by component quantization.\n"
        "   Template for evaluate_results:\n"
        "   LTR = RawRead(raw_path)\n"
        "   steps = LTR.get_steps()\n"
        "   t = LTR.get_trace('time').get_wave(steps[0])\n"
        "   v = LTR.get_trace('V(out)').get_wave(steps[0])\n"
        "   cut = int(len(t) * 0.7)\n"
        "   v_ss = v[cut:]\n"
        "   metrics['V_mean'] = float(np.mean(v_ss))\n"
        "   metrics['V_ripple_pp'] = float(np.ptp(v_ss))\n"
        "5. Active Compensation: Quantizing passive components shifts V_mean slightly away from 5.00 V. "
        "Fine-tune the active control knob (Ton) by small increments (e.g. 3.75u - 3.82u) to restore V_mean = 5.00 V +/- 0.05 V "
        "and maintain ripple < 0.05 V.\n"
        "6. Conclude: Once you achieve specs with 100% commercial discrete components, report the final Bill of Materials (BOM)."
    )
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            "Begin the Discrete Sizing Phase. "
            "First query the database for available standard L and C parts, select the most suitable "
            "commercial discrete values, simulate the quantized circuit, and then perform active duty-cycle "
            "compensation to satisfy all target tolerances."
        ))
    ]
    
    current_metrics = dict(init_metrics)
    iteration = 1
    tool_map = {t.name: t for t in tools}
    
    print(f"\nStarting autonomous Discrete Sizing ({provider} - {model_name or ('gpt-4o' if provider=='openai' else 'gemini-3.5-flash')})...")
    
    while iteration <= max_iterations:
        print(f"\n--- Sizer Iteration {iteration} ---")
        tracker.total_llm_calls += 1
        ai_msg = model.invoke(messages)
        messages.append(ai_msg)
        
        if ai_msg.content:
            print(f"Agent Reasoning: {ai_msg.content[:200]}...")
            
        if not ai_msg.tool_calls:
            print("Discrete Sizing Agent completed.")
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
                    note = f"Discrete Sizing Step (L={current_params.get('L_nom')}, C={current_params.get('C_nom')}, Ton={current_params.get('Ton')})"
                    tracker.log_step(iteration, current_params, current_metrics, note)
                except Exception as ex:
                    print(f"Error parsing metrics: {ex}")
                    
        # Check termination condition:
        v_m = current_metrics.get("V_mean", 0.0)
        v_r = current_metrics.get("V_ripple_pp", 1.0)
        l_curr = current_params.get("L_nom", "")
        c_curr = current_params.get("C_nom", "")
        is_discrete = (l_curr in ["10u", "15u", "22u"]) and (c_curr in ["100u", "120u", "150u", "180u", "220u", "330u"])
        
        if is_discrete and (4.95 <= v_m <= 5.05) and (v_r < 0.05) and iteration >= 3:
            print("\n" + "="*50)
            print(f"SUCCESS! Discrete Sizing & Compensation ACHIEVED at iteration {iteration}:")
            print(f"  V_mean = {v_m:.4f} V (Target: 5.00 V +/- 0.05 V)")
            print(f"  V_ripple_pp = {v_r:.4f} V (Target: < 0.05 V)")
            print(f"  Commercial Components: L={l_curr}, C={c_curr}, Compensated Ton={current_params.get('Ton')}")
            print("="*50)
            break
            
        iteration += 1
        
    # Save summary files
    tracker.save_summary()
    
    # -------------------------------------------------------------
    # Generate Publication-Quality Comparison Figure
    # -------------------------------------------------------------
    try:
        raw_final = RawRead(target_raw)
        s_fin = raw_final.get_steps()[0]
        t_fin = raw_final.get_trace("time").get_wave(s_fin)
        v_fin = raw_final.get_trace("V(out)").get_wave(s_fin)
        
        # Load baseline continuous
        raw_base = RawRead(raw_cont_path)
        s_base = raw_base.get_steps()[0]
        t_base = raw_base.get_trace("time").get_wave(s_base)
        v_base = raw_base.get_trace("V(out)").get_wave(s_base)
        
        has_quant = os.path.exists(raw_quantized_path)
        if has_quant:
            raw_q = RawRead(raw_quantized_path)
            s_q = raw_q.get_steps()[0]
            t_q = raw_q.get_trace("time").get_wave(s_q)
            v_q = raw_q.get_trace("V(out)").get_wave(s_q)
            
        plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
        
        # Panel 1: Full Transient (0 to 10 ms)
        ax1.plot(t_base * 1e3, v_base, label="Continuous Ideal Baseline ($L=14\\,\\mu\\mathrm{H},\\, C=135\\,\\mu\\mathrm{F},\\, T_{\\mathrm{on}}=3.80\\,\\mu\\mathrm{s}$)", color="#7570b3", linestyle="--", alpha=0.85)
        if has_quant:
            ax1.plot(t_q * 1e3, v_q, label="Discrete Quantized Uncompensated ($L=15\\,\\mu\\mathrm{H},\\, C=150\\,\\mu\\mathrm{F},\\, T_{\\mathrm{on}}=3.80\\,\\mu\\mathrm{s}$)", color="#d95f02", linestyle=":", alpha=0.75)
        ax1.plot(t_fin * 1e3, v_fin, label=f"Final Discrete Sized & Compensated ($L={current_params.get('L_nom')},\\, C={current_params.get('C_nom')},\\, T_{{\\mathrm{{on}}}}={current_params.get('Ton')}$)", color="#1b9e77", linewidth=2.0)
        ax1.axhline(5.0, color="gray", linestyle="-.", alpha=0.6, label="Target $V_{\\mathrm{out}} = 5.0\\,\\mathrm{V}$")
        ax1.set_ylabel("Output Voltage $V_{\\mathrm{out}}$ [V]", fontsize=11, fontweight="bold")
        ax1.set_title("Stage 3: Full Transient Response — Continuous Ideal vs. Discrete Commercial Realization", fontsize=12, fontweight="bold")
        ax1.legend(loc="lower right", frameon=True, fontsize=9)
        ax1.set_xlim(0, 10.0)
        
        # Panel 2: Steady-State Ripple Zoom (9.8 ms to 10.0 ms)
        mask_base = t_base >= 0.0098
        mask_fin = t_fin >= 0.0098
        ax2.plot(t_base[mask_base] * 1e3, v_base[mask_base], label="Continuous Baseline", color="#7570b3", linestyle="--", linewidth=1.8)
        if has_quant:
            mask_q = t_q >= 0.0098
            ax2.plot(t_q[mask_q] * 1e3, v_q[mask_q], label="Discrete Uncompensated", color="#d95f02", linestyle=":", linewidth=1.8)
        ax2.plot(t_fin[mask_fin] * 1e3, v_fin[mask_fin], label="Final Compensated Discrete BOM", color="#1b9e77", linewidth=2.2)
        ax2.axhline(5.0, color="green", linestyle="--", alpha=0.7, label="Target (5.00 V)")
        ax2.axhspan(4.95, 5.05, color="green", alpha=0.15, label="Tolerance Band (+/- 1%)")
        ax2.set_xlabel("Time [ms]", fontsize=11, fontweight="bold")
        ax2.set_ylabel("Output Voltage $V_{\\mathrm{out}}$ [V]", fontsize=11, fontweight="bold")
        ax2.set_title("Steady-State Voltage Regulation & Ripple Window Zoom ($t \\in [9.8, 10.0]\\,\\mathrm{ms}$)", fontsize=12, fontweight="bold")
        ax2.legend(loc="upper right", frameon=True, fontsize=9)
        ax2.set_xlim(9.8, 10.0)
        
        plt.tight_layout()
        plot_path = os.path.join(results_dir, f"discrete_sizing{provider_tag}.png")
        plt.savefig(plot_path, dpi=300)
        plt.close()
        print(f"Publication-quality figure saved to: {plot_path}")
    except Exception as e:
        print(f"Plot generation error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Discrete Component Sizing Experiment")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="openai")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max_iterations", type=int, default=12)
    args = parser.parse_args()
    
    run_discrete_sizing(provider=args.provider, model_name=args.model, max_iterations=args.max_iterations)
