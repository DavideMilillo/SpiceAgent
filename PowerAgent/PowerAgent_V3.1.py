"""
SpiceAgent - PowerAgent V3.1 (Live/Safe Modes)
A Two-Stage AI Agent for optimizing ANY power electronics circuit.

Architecture:
1. Stage 1 (Consultant): interactive chat to define goals, analyze circuit, and build a spec.
2. Stage 2 (Engineer): Autonomous loop that optimizes the circuit.

V3.1 Config:
- Adds User Option: 'Safe Mode' (Isolated) vs 'Live Mode' (Direct Diagram Update).
- Live Mode enables real-time updates to the .asc file so the user sees changes.

Author: Davide Milillo 
"""

import os
import sys
import operator
import shutil
import ast
import traceback
import re
import matplotlib.pyplot as plt
import numpy as np
from typing import Annotated, List, Dict, Any, Union, Optional
from typing_extensions import TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from PyLTSpice import SimRunner, SpiceEditor, RawRead

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results_v3")
os.makedirs(RESULTS_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(RESULTS_DIR, "Agent_Memory_V3.0.md")

# --- Shared Utilities ---

def log_memory(message: str):
    """Logs a message to the Agent_Memory_V3.0.md file."""
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{message}\n\n")

def reset_memory():
    """Resets the Agent_Memory_V3.0.md file."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write("# PowerAgent V3.1 Memory Log\n\n")

def clean_filename(path: str) -> str:
    return os.path.basename(path)

def reload_ltspice_live(circuit_path: str):
    """
    Uses ctypes (Dependency-Free) to send Ctrl+F4 to LTSpice and reload.
    This avoids 'win32api' DLL errors common in Conda envs.
    """
    import time
    import ctypes
    from ctypes import wintypes
    
    # Constants
    VK_CONTROL = 0x11
    VK_F4 = 0x73
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1
    
    # Structures for SendInput
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_ulong)]
    
    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD),
                    ("ki", KEYBDINPUT)]
    
    def send_ctrl_f4():
        user32 = ctypes.windll.user32
        inputs = []
        
        # Ctrl Down
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, dwFlags=0)))
        # F4 Down
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_F4, dwFlags=0)))
        # F4 Up
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_F4, dwFlags=KEYEVENTF_KEYUP)))
        # Ctrl Up
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, dwFlags=KEYEVENTF_KEYUP)))
        
        pInputs = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), pInputs, ctypes.sizeof(INPUT))

    try:
        user32 = ctypes.windll.user32
        
        # 1. Find LTSpice Window
        found_hwnd = []
        def enum_proc(hwnd, lParam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if "LTspice" in title:
                    found_hwnd.append(hwnd)
            return True
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        
        if not found_hwnd:
            return False

        hwnd = found_hwnd[-1] # Take the last one found
        
        # 2. Bring to Foreground
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9) # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5) # Wait for focus
        
        # 3. Send Close Command (Ctrl+F4 to close active doc)
        send_ctrl_f4()
        time.sleep(0.5)
        
        # 4. Handle Save Popup (Blindly press 'n' just in case)
        # We simulate pressing 'n'
        inputs_n = [
            INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=ord('N'), dwFlags=0)),
            INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=ord('N'), dwFlags=KEYEVENTF_KEYUP))
        ]
        user32.SendInput(2, (INPUT * 2)(*inputs_n), ctypes.sizeof(INPUT))
        time.sleep(0.2)

        # 5. Re-open file
        if os.path.exists(circuit_path):
            os.startfile(circuit_path)
            
        return True

    except Exception as e:
        print(f"  [Live] Error during refresh: {e}")
        return False

def modify_asc_file(asc_path: str, changes: Dict[str, str]):
    """
    Directly modifies the .asc file text to update component values and parameters.
    This allows the user to see changes in real-time in the schematic.
    
    Supports:
    - Component Values (SYMATTR Value X)
    - Global Parameters (.param X=Y)
    """
    try:
        with open(asc_path, 'r', encoding='latin-1') as f:
            lines = f.readlines()

        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # 1. Handle Components (SYMBOL block)
            if line.strip().startswith('SYMBOL'):
                # Read the whole symbol block
                block = [line]
                i += 1
                while i < len(lines) and not (lines[i].strip().startswith('SYMBOL') or lines[i].strip().startswith('WIRE') or lines[i].strip().startswith('TEXT') or lines[i].strip().startswith('FLAG')):
                    block.append(lines[i])
                    i += 1
                
                # Analyze block
                inst_name = None
                value_line_idx = -1
                
                for idx, bline in enumerate(block):
                    if 'SYMATTR InstName' in bline:
                        parts = bline.strip().split('SYMATTR InstName')
                        if len(parts) > 1:
                            inst_name = parts[1].strip()
                    if 'SYMATTR Value' in bline:
                        value_line_idx = idx
                
                if inst_name and inst_name in changes and value_line_idx != -1:
                    # Update Value
                    # block[value_line_idx] = f"SYMATTR Value {changes[inst_name]}\n"
                    # Preserve line ending
                    block[value_line_idx] = f"SYMATTR Value {changes[inst_name]}\n"
                
                new_lines.extend(block)
                continue # Loop continues at current i

            # 2. Handle Global Parameters inside TEXT objects
            # Typical Format: TEXT -32 40 Left 2 !.param R1=10k
            if '!.param' in line or '.param' in line:
                for name, val in changes.items():
                    # Simple regex replace for "name=old_val" or "name old_val"
                    # We match word boundary for name, then =, then value
                    # Try name=val pattern
                    pattern = rf"({name}\s*=\s*)([^ \n\r\t]+)"
                    if re.search(pattern, line):
                        line = re.sub(pattern, rf"\1{val}", line)
                    else:
                        # Try name val pattern (less common in .param but possible)
                        pattern_space = rf"({name}\s+)([^ \n\r\t]+)"
                        if re.search(pattern_space, line):
                             line = re.sub(pattern_space, rf"\1{val}", line)
                new_lines.append(line)
                i+=1
                continue
            
            new_lines.append(line)
            i += 1
            
        with open(asc_path, 'w', encoding='latin-1') as f:
            f.writelines(new_lines)

    except Exception as e:
        print(f"Warning: Could not update .asc file directly: {e}")


def parameterize_netlist(netlist_path: str, tunable_components: List[str]) -> List[str]:
    """
    The 'Parametrizator': Uses the LLM to intelligently convert hardcoded components 
    to parameterized ones in the netlist.
    """
    import json
    
    print(f"  -> [Parametrizator] Invoking LLM to parameterize: {tunable_components}")
    log_memory(f"**[Parametrizator]**: Invoking LLM to parameterize: {tunable_components}")
    
    try:
        with open(netlist_path, 'r', encoding='latin-1') as f:
            content = f.read()
            
        model = ChatOpenAI(model="gpt-4o", temperature=0)
        
        system_prompt = (
            "You are the 'Netlist Architect', an expert in SPICE circuit syntax.\n"
            "Your Task: specific components in the provided netlist must be made 'tunable'.\n"
            "1. Receive the netlist and a list of Component Names.\n"
            "2. For each component, find its definition line.\n"
            "3. Extract its current value (e.g., '10k', '22u', 'PULSE(...)').\n"
            "4. Create a new parameter name for it (e.g., 'val_R1').\n"
            "   - If it's a PULSE source, we specifically want to tune the 'Ton' (On Time). Extract it and parameterize it as 'Ton_{Name}'.\n"
            "   - If it's a standard passives (R, L, C) or source, parameterize the main value/magnitude.\n"
            "5. REWRITE the netlist replacing that value with the parameter in curly braces: '{val_R1}'.\n"
            "6. APPEND the .param definition at the end (e.g., '.param val_R1=10k').\n"
            "7. Return JSON with the 'new_netlist' and the list of 'new_parameter_names'.\n"
            "IMPORTANT: Preserve all other text (node names, parasitic properties like Rser, etc) exactly."
        )
        
        user_prompt = (
            f"Components to parameterize: {tunable_components}\n\n"
            f"Netlist Content:\n```\n{content}\n```"
        )
        
        response = model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        txt = response.content.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(txt)
        except json.JSONDecodeError:
            print("  -> [Parametrizator] JSON parse error. Raw response:\n" + txt[:200])
            return tunable_components
        
        new_netlist = data.get("new_netlist") or data.get("netlist")
        if not new_netlist:
             print("  -> [Parametrizator] Error: LLM returned empty netlist.")
             return tunable_components

        new_params = data.get("new_parameter_names", [])
        
        with open(netlist_path, 'w', encoding='latin-1') as f:
            f.write(new_netlist)
            
        print(f"  -> [Parametrizator] Success. New params: {new_params}")
        log_memory(f"**[Parametrizator]**: Success. New params: {new_params}")
        return new_params

    except Exception as e:
        print(f"  -> [Parametrizator] Error: {e}. Keeping original list.")
        log_memory(f"**[Parametrizator]**: Error: {e}")
        return tunable_components

# =================================================================================================
# STAGE 1: THE CONSULTANT
# =================================================================================================

class OptimizationSpecs(TypedDict):
    circuit_path: str
    target_netlist: str
    target_raw: str
    tunable_parameters: List[str] 
    optimization_goals: Union[str, Dict[str, Any]]
    metric_extraction_hint: str

class ConsultantState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    circuit_path: str
    available_components: List[str]
    available_params: List[str]
    is_ready: bool
    specs: Optional[OptimizationSpecs]

@tool
def analyze_circuit_structure(file_path: str) -> str:
    """Parses a LTSpice Circuit (.asc or .net) to find all Components and Parameters."""
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    
    try:
        netlist = SpiceEditor(file_path)
        components = netlist.get_components()
        
        params = []
        with open(file_path, 'r', encoding='latin-1') as f:
            content_netlist= f.read()
            for line in f:
                line = line.strip()
                if line.lower().startswith('.param'):
                    parts = line.split()
                    if len(parts) > 1:
                        params.append(line)
        
        return (f"Analysis of {os.path.basename(file_path)}:\n"
                f"--- Parameters (.param) ---\n" + "\n".join(params) + "\n\n"
                f"--- Components ---\n" + ", ".join(components[:20]) + 
                (f"... (+{len(components)-20} more)" if len(components)>20 else "") + "\n"
                f"Whole netlist: {content_netlist}"
                "\nUsage: specific components can be updated directly, or parameters can be tuned.")
    except Exception as e:
        return f"Error parsing circuit: {str(e)}"

def consultant_node(state: ConsultantState):
    system_prompt = (
        "You are an Expert Power Electronics Consultant. "
        "Your goal is to prepare a robust 'Optimization Specification' for the engineering agent.\n"
        "1. Analyze the circuit provided by the user using `analyze_circuit_structure`.\n"
        "2. Discuss with the user to identify:\n"
        "   - Which parameters/components are 'tunable' (the knobs).\n"
        "   - What are the precise goals (Vout target, efficiency, etc.).\n"
        "   - Which nodes/traces correspond to these goals (e.g. 'Is V(n001) the output?').\n"
        "3. Once you have ALL info, output ONLY the FINAL CONFIRMATION in this EXACT format:\n"
        "   'READY_TO_OPTIMIZE: {JSON_representation_of_OptimizationSpecs}'\n"
        "   Make sure tunable_parameters are exact names found in the file."
    )
    
    messages = [SystemMessage(content=system_prompt)] + state['messages']
    model = ChatOpenAI(model="gpt-4o", temperature=0.1)
    model = model.bind_tools([analyze_circuit_structure])
    response = model.invoke(messages)
    log_memory(f"**[Consultant]**: {response.content}")
    return {"messages": [response]}

def tools_consultant_node(state: ConsultantState):
    tools = [analyze_circuit_structure]
    return ToolNode(tools).invoke(state)

# =================================================================================================
# STAGE 2: THE ENGINEER
# =================================================================================================

class EngineerState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    specs: OptimizationSpecs
    iteration: int
    current_metrics: Dict[str, float]
    best_metrics: Dict[str, float]

# --- Engineer Tools Factory ---

def create_engineer_tools(work_dir: str, netlist_name: str, raw_name: str, asc_path: Optional[str] = None):
    
    netlist_path = os.path.join(work_dir, netlist_name)
    raw_path = os.path.join(work_dir, raw_name)

    @tool
    def update_circuit(changes: Dict[str, str]) -> str:
        """
        Updates components or parameters in the netlist.
        Args:
            changes: Dictionary { 'name': 'value' }. 
                     Examples: {'R1': '10k', 'Cout': '22u', 'duty_cycle': '0.5'}
        """
        try:
            # Robustness: Check if .net exists, if not and we have .asc, regenerate it
            if not os.path.exists(netlist_path):
                if asc_path and os.path.exists(asc_path):
                     # Try to repair missing netlist from Live source
                     try:
                         tmp_editor = SpiceEditor(asc_path)
                         tmp_editor.write_netlist(netlist_path)
                     except: 
                         pass # Fall through to error
            
            # Now try to load
            netlist = SpiceEditor(netlist_path)
            log = []
            
            for name, val in changes.items():
                try:
                    netlist.set_parameter(name, val)
                    log.append(f"Set .param {name} = {val}")
                except:
                    try:
                        netlist.set_component_value(name, val)
                        log.append(f"Set component {name} value = {val}")
                    except:
                        try:
                             netlist.set_element_model(name, val)
                             log.append(f"Set model {name} = {val}")
                        except Exception as e:
                             log.append(f"Failed to update {name}: {e}")
            
            # Write key update to Netlist for simulation
            netlist.write_netlist(netlist_path)
            
            # If Live Mode activated: Update the .asc file visually
            if asc_path and os.path.exists(asc_path):
                try:
                    modify_asc_file(asc_path, changes)
                    log.append(f"[Live] Updated schema file: {asc_path}")
                    
                    # Force Reload (Close RAM -> Read from Disk)
                    if reload_ltspice_live(asc_path):
                         log.append(f"[Live] Forced LTSpice to reload file.")
                    else:
                         log.append(f"[Live] Info: Click on LTSpice window to see updates (Auto-reload failed).")
                         
                except Exception as e:
                    log.append(f"[Live] Failed to update .asc: {e}")

            log_str = "Updates applied:\n" + "\n".join(log)
            log_memory(f"**[Engineer Tool Update]**: {log_str}")
            return log_str
        except Exception as e:
            return f"Error updating circuit: {e}"

    @tool
    def simulate_circuit() -> str:
        """Runs the LTSpice simulation. Returns the output RAW filename if successful."""
        try:
            runner = SimRunner(output_folder=work_dir)
            netlist = SpiceEditor(netlist_path)
            runner.run(netlist, run_filename=netlist_name)
            runner.wait_completion()
            
            if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
                msg = f"Simulation success. Output: {os.path.basename(raw_path)}"
                log_memory(f"**[Engineer Tool Sim]**: {msg}")
                return msg
            else:
                fail_log = os.path.join(work_dir, netlist_name.replace('.net', '.fail'))
                log_file = os.path.join(work_dir, netlist_name.replace('.net', '.log'))
                error_detail = ""
                if os.path.exists(fail_log):
                    with open(fail_log, 'r', errors='ignore') as f: error_detail = f.read()
                elif os.path.exists(log_file):
                    with open(log_file, 'r', errors='ignore') as f: error_detail = f.read()
                
                return f"Simulation failed/No RAW.\nLog: {error_detail[-500:]}"
        except Exception as e:
            return f"Error interacting with LTSpice: {e}"

    @tool
    def evaluate_results(python_script: str) -> str:
        """Executes a Python script to extract metrics from the simulation 'raw' file."""
        # 1. Safety Check for common Agent hallucinations
        if "import ltspice" in python_script:
            return (
                "Error: The 'ltspice' module is NOT available. "
                "Do NOT try to import it. "
                "Use the 'RawRead' class which is ALREADY available in your scope. "
                "Example: `LTR = RawRead(raw_path)`."
            )

        try:
            log_memory(f"**[Engineer Tool Analysis Script]**:\n```python\n{python_script}\n```")
            local_scope = {'raw_path': raw_path, 'RawRead': RawRead, 'np': np, 'metrics': {}}
            exec(python_script, globals(), local_scope)
            metrics = local_scope.get('metrics', {})
            if metrics:
                log_memory(f"**[Engineer Tool Metrics]**: {metrics}")
                return f"Computed Metrics: {metrics}"
            else:
                return "Script ran but 'metrics' dict empty."
        except Exception as e:
            return f"Error executing analysis script:\n{traceback.format_exc()}"

    @tool
    def ask_human(question: str) -> str:
        """Asks the human user a question or requests feedback."""
        print(f"\n[Engineer Asking]: {question}")
        log_memory(f"**[Engineer Asking]**: {question}")
        response = input("\n[You]: ")
        if response.lower() in ['exit', 'quit']:
            sys.exit(0)
        log_memory(f"**[You]**: {response}")
        return response

    @tool
    def read_netlist() -> str:
        """Reads the current content of the netlist file."""
        try:
            with open(netlist_path, 'r', encoding='latin-1') as f: return f.read()
        except Exception as e: return str(e)

    return [update_circuit, simulate_circuit, evaluate_results, ask_human, read_netlist]

# --- Engineer Node ---

def engineer_node(state: EngineerState):
    iteration = state['iteration']
    specs = state['specs']
    
    out_node = specs.get('output_node', 'unknown')
    metrics_hint = specs.get('metric_extraction_hint', f"Output Node: {out_node}")

    context = (
        f"Specs: {specs.get('optimization_goals', 'Meet requirements')}\n"
        f"Tunable Params: {specs.get('tunable_parameters', [])}\n"
        f"Metrics Hint: {metrics_hint}\n"
        f"Iteration: {iteration}"
    )
    
    messages = [SystemMessage(content=context)] + state['messages']
    model = ChatOpenAI(model="gpt-4o", temperature=0.1)
    
    work_dir = os.path.dirname(specs['target_raw'])
    netlist_name = os.path.basename(specs['target_netlist'])
    raw_name = os.path.basename(specs['target_raw'])
    
    # Grab asc_path if it was stuffed into specs, or infer it
    asc_path = specs.get('live_asc_path', None)

    tools = create_engineer_tools(work_dir, netlist_name, raw_name, asc_path)
    model = model.bind_tools(tools)
    response = model.invoke(messages)
    
    return {"messages": [response], "iteration_count": iteration + 1}

def engineer_tools_node(state: EngineerState):
    specs = state['specs']
    work_dir = os.path.dirname(specs['target_raw'])
    netlist_name = os.path.basename(specs['target_netlist'])
    raw_name = os.path.basename(specs['target_raw'])
    asc_path = specs.get('live_asc_path', None)
    
    tools = create_engineer_tools(work_dir, netlist_name, raw_name, asc_path)
    return ToolNode(tools).invoke(state)

# =================================================================================================
# MAIN FLOW
# =================================================================================================

def run_consultant_phase(circuit_path: str):
    print("\n--- PHASE 1: CONSULTANT ---")
    print(f"Analyzing {circuit_path}...")
    
    initial_msg = (
        f"I have loaded the circuit file: {circuit_path}. "
        "Please analyze it and tell me what you see, and verify if I can optimize it for you."
    )
    
    state: ConsultantState = {
        "messages": [HumanMessage(content=initial_msg)],
        "circuit_path": circuit_path,
        "available_components": [],
        "available_params": [],
        "is_ready": False,
        "specs": None
    }
    
    workflow = StateGraph(ConsultantState)
    workflow.add_node("consultant", consultant_node)
    workflow.add_node("tools", tools_consultant_node)
    
    def should_continue(state):
        last_msg = state['messages'][-1]
        if last_msg.tool_calls: return "tools"
        if "READY_TO_OPTIMIZE:" in last_msg.content: return END
        return END

    workflow.add_conditional_edges("consultant", should_continue)
    workflow.add_edge("tools", "consultant")
    workflow.set_entry_point("consultant")
    app = workflow.compile()
    
    while True:
        result = app.invoke(state)
        state = result
        last_msg = state['messages'][-1]
        print(f"\n[Consultant]: {last_msg.content}")
        
        if "READY_TO_OPTIMIZE:" in last_msg.content:
            try:
                import json
                json_str = last_msg.content.split("READY_TO_OPTIMIZE:", 1)[1].strip()
                if json_str.startswith("```"): json_str = json_str.strip("`json ")
                specs = json.loads(json_str)
                specs['circuit_path'] = circuit_path
                return specs
            except Exception as e:
                print(f"Error parsing specs: {e}")

        user_input = input("\n[You]: ")
        if user_input.lower() in ['exit', 'quit']: sys.exit(0)
        state['messages'].append(HumanMessage(content=user_input))

def run_engineer_phase(specs: OptimizationSpecs):
    print("\n--- PHASE 2: ENGINEER ---")
    goals = specs.get('optimization_goals') or specs.get('optimization_goal') or "Optimize Circuit"
    print(f"Goal: {goals}")
    specs['optimization_goals'] = goals

    # --- USER CHOICE: MODE SELECTION ---
    print("\n[Configuration] Select Operation Mode:")
    print("1. Safe Mode (Recommended): Works in a separate copy. No changes to your .asc file.")
    print("2. Live Mode (Experimental): Works DIRECTLY on your .asc file. You see values change in LTSpice.")
    
    mode = input("Select option (1/2): ").strip()
    is_live = mode == '2'
    
    if is_live:
        print(">> Selected: LIVE MODE. WARNING: Your .asc file will be modified.")
        work_dir = os.path.dirname(specs['circuit_path'])
        
        original_net = specs['circuit_path']
        # We need to run simulation on a netlist
        target_net = original_net.replace('.asc', '.net')
        if target_net == original_net: target_net += ".net"
        
        target_raw = target_net.replace('.net', '.raw')
        
        # In Live Mode, we act on the original file
        specs['live_asc_path'] = original_net
        
        # Skip automatic parameterization usually, to preserve simple User values
        # Or ask user? We'll skip for now to keep the file 'clean' as per visual requirement
        print("   (Skipping automatic parameterization to maintain visual clarity)")

    else:
        print(">> Selected: SAFE MODE.")
        work_dir = RESULTS_DIR
        original_net = specs['circuit_path']
        target_net = os.path.join(work_dir, "optimized_design.net")
        target_raw = os.path.join(work_dir, "optimized_design.raw")
        
        shutil.copy(original_net, os.path.join(work_dir, "source.asc"))
        specs['live_asc_path'] = None
        
        
    try:
        # Initialize netlist
        net = SpiceEditor(specs['circuit_path']) # Read original
        net.write_netlist(target_net)          # Write working netlist
        
        # Apply Parameterization ONLY in Safe Mode or if strictly needed
        if not is_live:
            print("  -> Applying circuit parameterization...")
            new_params = parameterize_netlist(target_net, specs['tunable_parameters'])
            specs['tunable_parameters'] = new_params
            print(f"  -> Tunable parameters updated: {new_params}")
            
    except Exception as e:
        print(f"Error initializing netlist: {e}")
        return

    # Update Specs
    specs['target_netlist'] = target_net
    specs['target_raw'] = target_raw
    
    engineer_sys_prompt = (
        "You are a 'Human-in-the-Loop' Power Engineering Assistant.\n"
        "Your goal is to help your human user optimize the circuit, but you acknowledge they are smarter than you.\n"
        "You operate in a loop:\n"
        "1. Update Circuit: Adjust 'tunable_parameters' to move towards goal.\n"
        "2. Simulate: Run the simulation.\n"
        "3. Evaluate: Write PYTHON CODE to inspect the RAW file and extract metrics.\n"
        "4. COMMUNICATE: Use the `ask_human` tool to report results, ask for guidance, or confirm completion.\n"
        "   - NEVER just stop or output text without using a tool. If you want to talk to the human, use `ask_human`.\n"
        "   - If you are stuck or Vout isn't changing, ASK THE HUMAN.\n"
        "   - If you think you are done, ASK THE HUMAN to confirm.\n\n"
        "IMPORTANT on Python Scripting:\n"
        " - WARNING: The `ltspice` library is NOT installed. DO NOT try to import it.\n"
        " - WARNING: DO NOT ask the user to install libraries. You must use the embedded tool.\n"
        " - ALWAYS use `PyLTSpice.RawRead` which is pre-loaded in the global scope as `RawRead`.\n"
        " - Always assign the final dict of values to variable `metrics`.\n"
        " - Available in scope: `raw_path`, `RawRead`, `np`.\n"
        " - PyLTSpice Usage CHEATSHEET (Use this structure):\n"
        "   ```python\n"
        "   LTR = RawRead(raw_path)\n"
        "   # 1. Get dictionary of trace names to find exact node name (e.g. V(n002))\n"
        "   # print(LTR.get_trace_names())\n"
        "   \n"
        "   # 2. Extract Time and Voltage vectors (step 0 for transients)\n"
        "   steps = LTR.get_steps()\n"
        "   t = LTR.get_trace('time').get_wave(steps[0])\n"
        "   # Note: use case-sensitive trace name from LTR.get_trace_names() if unsure\n"
        "   v_node = LTR.get_trace('V(out)').get_wave(steps[0])\n"
        "   \n"
        "   # 3. Process Data (e.g. steady state last 30%)\n"
        "   cut_idx = int(len(t) * 0.7)\n"
        "   v_ss = v_node[cut_idx:]\n"
        "   metrics = {\n"
        "       'v_mean': np.mean(v_ss),\n"
        "       'ripple_pp': np.ptp(v_ss) # peak-to-peak\n"
        "   }\n"
        "   ```\n"
    )

    initial_state: EngineerState = {
        "messages": [SystemMessage(content=engineer_sys_prompt), HumanMessage(content="Start optimization.")],
        "specs": specs, "iteration": 0, "current_metrics": {}, "best_metrics": {}
    }
    
    workflow = StateGraph(EngineerState)
    workflow.add_node("engineer", engineer_node)
    workflow.add_node("tools", engineer_tools_node)
    
    def should_continue_engineer(state):
        return "tools" if state['messages'][-1].tool_calls else END

    workflow.add_conditional_edges("engineer", should_continue_engineer, {"tools": "tools", END: END})
    workflow.add_edge("tools", "engineer")
    workflow.set_entry_point("engineer")
    app = workflow.compile()
    
    print("Engineer Agent is running... (Type 'exit' to quit at any prompt)")
    for event in app.stream(initial_state, config={"recursion_limit": 100}):
        if 'engineer' in event:
            msg = event['engineer']['messages'][-1]
            if not msg.tool_calls:
                print(f"\n[Engineer Text]: {msg.content}")
                user_reply = input("\n[You (Implicit Ask)]: ")
                if user_reply.lower() in ['exit', 'quit']: sys.exit(0)

def main():
    print("=== PowerAgent V3.1 ===")
    reset_memory()
    log_memory("# PowerAgent V3.1 Optimization Session")
    
    default_circuit = None
    search_dirs = [BASE_DIR, os.path.join(BASE_DIR, '..', 'Circuits')]
    for d in search_dirs:
        if os.path.exists(d):
            files = [f for f in os.listdir(d) if f.endswith('.asc')]
            if files:
                default_circuit = os.path.join(d, files[0])
                break
    
    circuit_path = input(f"Enter circuit path [{default_circuit or 'path/to/file.asc'}]: ").strip()
    if not circuit_path and default_circuit: circuit_path = default_circuit
        
    if not circuit_path or not os.path.exists(circuit_path):
        print("Invalid circuit path.")
        return

    specs = run_consultant_phase(circuit_path)
    if not specs: return
        
    run_engineer_phase(specs)

if __name__ == "__main__":
    main()
