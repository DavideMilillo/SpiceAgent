"""
SpiceAgent - PowerAgent V3 (Interactive Agent)
A Two-Stage AI Agent for optimizing ANY power electronics circuit.

Architecture:
1. Stage 1 (Consultant): interactive chat to define goals, analyze circuit, and build a spec.
2. Stage 2 (Engineer): Autonomous loop that optimizes the circuit.

This module provides the InteractiveAgent class.
"""

import os
import sys
import operator
import shutil
import ast
import traceback
import re
import importlib.resources
import json
import logging
# Optional imports for plotting if needed, though mostly handled in tool logic
# import matplotlib.pyplot as plt 
import numpy as np

# LangChain / Graph
from typing import Annotated, List, Dict, Any, Union, Optional
from typing_extensions import TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# Spice tools
from PyLTSpice import SimRunner, SpiceEditor, RawRead

# Setup access to resources using a consistent package-friendly way is tricky 
# if we want to write results relative to the USER's script, not the package install.
# We will adopt a "current working directory" policy for outputs unless specified.

class OptimizationSpecs(TypedDict):
    circuit_path: str
    target_netlist: str
    target_raw: str
    tunable_parameters: List[str] 
    optimization_goals: Union[str, Dict[str, Any]]
    metric_extraction_hint: str
    output_node: Optional[str]
    live_asc_path: Optional[str]

class ConsultantState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    circuit_path: str
    available_components: List[str]
    available_params: List[str]
    is_ready: bool
    specs: Optional[OptimizationSpecs]

class EngineerState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    specs: OptimizationSpecs
    iteration: int
    current_metrics: Dict[str, float]
    best_metrics: Dict[str, float]


class PowerAgentV3:
    """
    The V3 Agent featuring Consultant and Engineer modes with Live/Safe operations.
    Designed to be instantiated and run by end-users in scripts or CLI tools.
    """
    
    def __init__(self, output_dir: str = "agent_results", history_file: str = "Agent_Memory.md"):
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.memory_file = os.path.join(self.output_dir, history_file)
        self._reset_memory()
        
    def _reset_memory(self):
        """Resets the memory log file."""
        with open(self.memory_file, "w", encoding="utf-8") as f:
            f.write("# PowerAgent V3 Optimization Memory Log\n\n")

    def _log_memory(self, message: str):
        """Logs a message to the memory file."""
        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(f"{message}\n\n")

    # --- Windows Live Mode Utils (Static / Helper methods) ---
    
    @staticmethod
    def _reload_ltspice_live(circuit_path: str) -> bool:
        """
        Uses ctypes to CLOSE the LTSpice application fully and reload it.
        Windows Only.
        """
        if sys.platform != 'win32':
            print("  [Live] GUI Reloading is only supported on Windows.")
            return False

        try:
            import time
            import ctypes
            from ctypes import wintypes
            
            # Constants
            WM_CLOSE = 0x0010
            KEYEVENTF_KEYUP = 0x0002
            INPUT_KEYBOARD = 1
            
            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD),
                            ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_ulong)]
            
            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD),
                            ("ki", KEYBDINPUT)]

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
            
            # 2. Close ALL found LTSpice instances
            for hwnd in found_hwnd:
                 user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                 
                 if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)
                 user32.SetForegroundWindow(hwnd)
                 
                 # Close 'Do you want to save' dialogs with 'No' (N key)
                 time.sleep(0.2)
                 inputs_n = [
                    INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=ord('N'), dwFlags=0)),
                    INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=ord('N'), dwFlags=KEYEVENTF_KEYUP))
                 ]
                 user32.SendInput(2, (INPUT * 2)(*inputs_n), ctypes.sizeof(INPUT))
                 
            time.sleep(1.0)

            # 3. Re-open file
            if os.path.exists(circuit_path):
                os.startfile(circuit_path)
                
            time.sleep(5.0)
            return True

        except Exception as e:
            print(f"  [Live] Error during refresh: {e}")
            return False

    @staticmethod
    def _modify_asc_file(asc_path: str, changes: Dict[str, str]):
        """
        Directly modifies the .asc file text to update component values/params.
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
                    block = [line]
                    i += 1
                    # Read block until end of component definition
                    while i < len(lines) and not (lines[i].strip().startswith('SYMBOL') or lines[i].strip().startswith('WIRE') or lines[i].strip().startswith('TEXT') or lines[i].strip().startswith('FLAG')):
                        block.append(lines[i])
                        i += 1
                    
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
                        block[value_line_idx] = f"SYMATTR Value {changes[inst_name]}\n"
                    
                    new_lines.extend(block)
                    continue 

                # 2. Handle Global Parameters inside TEXT objects
                # Typical Format: TEXT -32 40 Left 2 !.param R1=10k
                if ('!.param' in line or '.param' in line) and '=' in line:
                    for name, val in changes.items():
                        # Regex for "name=old_val"
                        pattern = rf"({name}\s*=\s*)([^ \n\r\t]+)"
                        if re.search(pattern, line):
                            line = re.sub(pattern, rf"\1{val}", line)
                        else:
                            # Regex for "name old_val"
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

    # --- Tool Definitions (Bound to instance for logging) ---

    def _create_consultant_tools(self):
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
                    content_netlist = f.read()
                    f.seek(0)
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
        
        return [analyze_circuit_structure]

    def _create_engineer_tools(self, work_dir: str, netlist_name: str, raw_name: str, asc_path: Optional[str] = None):
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
                # Robustness: Check if .net exists
                if not os.path.exists(netlist_path):
                    if asc_path and os.path.exists(asc_path):
                         try:
                             tmp_editor = SpiceEditor(asc_path)
                             tmp_editor.write_netlist(netlist_path)
                         except: 
                             pass 
                
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
                
                netlist.write_netlist(netlist_path)
                
                # Live Mode Visual Update
                if asc_path and os.path.exists(asc_path):
                    try:
                        self._modify_asc_file(asc_path, changes)
                        log.append(f"[Live] Updated schema file: {asc_path}") 
                        log.append(f"[Live] Visual update deferred to simulation completion.")                    
                    except Exception as e:
                        log.append(f"[Live] Failed to update .asc: {e}")

                log_str = "Updates applied:\n" + "\n".join(log)
                self._log_memory(f"**[Engineer Tool Update]**: {log_str}")
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
                    
                    # Live Mode Reload
                    if asc_path and os.path.exists(asc_path):
                         self._reload_ltspice_live(asc_path)
                         msg += " (GUI Reloaded)"

                    self._log_memory(f"**[Engineer Tool Sim]**: {msg}")
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
            if "import ltspice" in python_script:
                return (
                    "Error: The 'ltspice' module is NOT available. "
                    "Use the 'RawRead' class which is ALREADY available in your scope. "
                    "Example: `LTR = RawRead(raw_path)`."
                )

            try:
                self._log_memory(f"**[Engineer Tool Analysis Script]**:\n```python\n{python_script}\n```")
                # Important: RawRead and np must be available in local scope
                local_scope = {'raw_path': raw_path, 'RawRead': RawRead, 'np': np, 'metrics': {}}
                exec(python_script, globals(), local_scope)
                metrics = local_scope.get('metrics', {})
                if metrics:
                    self._log_memory(f"**[Engineer Tool Metrics]**: {metrics}")
                    return f"Computed Metrics: {metrics}"
                else:
                    return "Script ran but 'metrics' dict empty."
            except Exception as e:
                return f"Error executing analysis script:\n{traceback.format_exc()}"

        @tool
        def ask_human(question: str) -> str:
            """Asks the human user a question or requests feedback."""
            print(f"\n[Engineer Asking]: {question}")
            self._log_memory(f"**[Engineer Asking]**: {question}")
            response = input("\n[You]: ")
            if response.lower() in ['exit', 'quit']:
                sys.exit(0)
            self._log_memory(f"**[You]**: {response}")
            return response

        @tool
        def read_netlist() -> str:
            """Reads the current content of the netlist file."""
            try:
                with open(netlist_path, 'r', encoding='latin-1') as f: return f.read()
            except Exception as e: return str(e)

        return [update_circuit, simulate_circuit, evaluate_results, ask_human, read_netlist]

    # --- Parameterization with LLM ---

    def _parameterize_netlist(self, netlist_path: str, tunable_components: List[str]) -> List[str]:
        """
        Uses LLM to parameterize component values in the netlist to make them tunable.
        """
        print(f"  -> [Parametrizator] Invoking LLM to parameterize: {tunable_components}")
        self._log_memory(f"**[Parametrizator]**: Invoking LLM to parameterize: {tunable_components}")
        
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
                print("  -> [Parametrizator] JSON parse error.")
                return tunable_components
            
            new_netlist = data.get("new_netlist") or data.get("netlist")
            if not new_netlist:
                 print("  -> [Parametrizator] Error: LLM returned empty netlist.")
                 return tunable_components

            new_params = data.get("new_parameter_names", [])
            
            with open(netlist_path, 'w', encoding='latin-1') as f:
                f.write(new_netlist)
                
            print(f"  -> [Parametrizator] Success. New params: {new_params}")
            self._log_memory(f"**[Parametrizator]**: Success. New params: {new_params}")
            return new_params

        except Exception as e:
            print(f"  -> [Parametrizator] Error: {e}. Keeping original list.")
            return tunable_components

    # --- Workflow Nodes ---

    def _consultant_node(self, state: ConsultantState):
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
        tools = self._create_consultant_tools()
        model = model.bind_tools(tools)
        response = model.invoke(messages)
        self._log_memory(f"**[Consultant]**: {response.content}")
        return {"messages": [response]}

    def _consultant_tools_node(self, state: ConsultantState):
        tools = self._create_consultant_tools()
        return ToolNode(tools).invoke(state)

    def _engineer_node(self, state: EngineerState):
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
        asc_path = specs.get('live_asc_path', None)

        tools = self._create_engineer_tools(work_dir, netlist_name, raw_name, asc_path)
        model = model.bind_tools(tools)
        response = model.invoke(messages)
        
        return {"messages": [response], "iteration_count": iteration + 1}

    def _engineer_tools_node(self, state: EngineerState):
        specs = state['specs']
        work_dir = os.path.dirname(specs['target_raw'])
        print(f"Debug: ToolNode executing in {work_dir}") 
        netlist_name = os.path.basename(specs['target_netlist'])
        raw_name = os.path.basename(specs['target_raw'])
        asc_path = specs.get('live_asc_path', None)
        
        tools = self._create_engineer_tools(work_dir, netlist_name, raw_name, asc_path)
        return ToolNode(tools).invoke(state)

    # --- Main Execution Methods ---

    def run_consultant(self, circuit_path: str) -> Optional[OptimizationSpecs]:
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
        workflow.add_node("consultant", self._consultant_node)
        workflow.add_node("tools", self._consultant_tools_node)
        
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

    def run_engineer(self, specs: OptimizationSpecs, live_mode: bool = False):
        print("\n--- PHASE 2: ENGINEER ---")
        goals = specs.get('optimization_goals') or "Optimize Circuit"
        print(f"Goal: {goals}")
        specs['optimization_goals'] = goals

        if live_mode:
            print(">> Mode: LIVE. WARNING: Your .asc file will be modified.")
            work_dir = os.path.dirname(specs['circuit_path'])
            original_net = specs['circuit_path']
            # Target is the netlist version of the ASC
            target_net = original_net.replace('.asc', '.net')
            if target_net == original_net: target_net += ".net"
            target_raw = target_net.replace('.net', '.raw')
            
            specs['live_asc_path'] = original_net
            print("   (Skipping automatic parameterization in Live Mode)")
        else:
            print(">> Mode: SAFE.")
            work_dir = self.output_dir
            original_net = specs['circuit_path']
            target_net = os.path.join(work_dir, "optimized_design.net")
            target_raw = os.path.join(work_dir, "optimized_design.raw")
            
            shutil.copy(original_net, os.path.join(work_dir, "source.asc"))
            specs['live_asc_path'] = None

        try:
            # Initialize netlist
            net = SpiceEditor(specs['circuit_path']) 
            net.write_netlist(target_net)          
            
            if not live_mode:
                print("  -> Applying circuit parameterization...")
                new_params = self._parameterize_netlist(target_net, specs['tunable_parameters'])
                specs['tunable_parameters'] = new_params
                print(f"  -> Tunable parameters updated: {new_params}")
                
        except Exception as e:
            print(f"Error initializing netlist: {e}")
            return

        specs['target_netlist'] = target_net
        specs['target_raw'] = target_raw
        
        engineer_sys_prompt = (
            "You are a 'Human-in-the-Loop' Power Engineering Assistant.\n"
            "Your goal is to help your human user optimize the circuit.\n"
            "You operate in a loop:\n"
            "1. Update Circuit: Adjust 'tunable_parameters' to move towards goal.\n"
            "2. Simulate: Run the simulation.\n"
            "3. Evaluate: Write PYTHON CODE to inspect the RAW file and extract metrics.\n"
            "4. COMMUNICATE: Use the `ask_human` tool to report results, ask for guidance, or confirm completion.\n"
        )

        initial_state: EngineerState = {
            "messages": [SystemMessage(content=engineer_sys_prompt), HumanMessage(content="Start optimization.")],
            "specs": specs, "iteration": 0, "current_metrics": {}, "best_metrics": {}
        }
        
        workflow = StateGraph(EngineerState)
        workflow.add_node("engineer", self._engineer_node)
        workflow.add_node("tools", self._engineer_tools_node)
        
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
                    self._log_memory(f"**[Engineer Text]**: {msg.content}")
                    
                    user_reply = input("\n[You (Implicit Ask)]: ")
                    self._log_memory(f"**[You (Implicit Ask)]**: {user_reply}")
                    
                    if user_reply.lower() in ['exit', 'quit']: sys.exit(0)

    def optimize(self, circuit_path: str):
        """
        Interactive entry point for the agent.
        """
        self._reset_memory()
        self._log_memory("# PowerAgent V3 Optimization Session")
        
        if not os.path.exists(circuit_path):
            print(f"Error: Circuit not found at {circuit_path}")
            return

        specs = self.run_consultant(circuit_path)
        if not specs: 
            return
            
        # Mode Selection
        print("\n[Configuration] Select Operation Mode:")
        print("1. Safe Mode (Recommended): Works in a separate copy.")
        print("2. Live Mode (Experimental, Windows Only): Works DIRECTLY on your .asc file.")
        
        mode = input("Select option (1/2): ").strip()
        is_live = (mode == '2')
        
        self.run_engineer(specs, live_mode=is_live)
