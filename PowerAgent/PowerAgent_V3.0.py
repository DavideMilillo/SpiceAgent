"""
SpiceAgent - PowerAgent V3.0 (General Purpose Optimization)
A Two-Stage AI Agent for optimizing ANY power electronics circuit.

Architecture:
1. Stage 1 (Consultant): interactive chat to define goals, analyze circuit, and build a spec.
2. Stage 2 (Engineer): Autonomous loop that optimizes the circuit using Python code generation for metrics.

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
# Adjust these if running in a different environment
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results_v3")
os.makedirs(RESULTS_DIR, exist_ok=True)


# --- Shared Utilities ---

def clean_filename(path: str) -> str:
    return os.path.basename(path)

def parameterize_netlist(netlist_path: str, tunable_components: List[str]) -> List[str]:
    """
    Modifies the netlist to use parameters for the specified components.
    Returns the new list of tunable parameter names.
    This implements 'Solution 3' (Pre-processing/Parameterization) to make the circuit 'Agent-Friendly'.
    """
    try:
        net = SpiceEditor(netlist_path)
        new_tunable = []
        
        # Read raw content to find .params and component definitions
        with open(netlist_path, 'r', encoding='latin-1') as f:
            content = f.read()

        updates_made = False

        for comp in tunable_components:
            # 1. Get current definition
            try:
                val = net.get_component_value(comp)
            except:
                # Fallback for complex lines using regex
                val = ""
                match = re.search(f"^{comp}\\s+.*$", content, re.MULTILINE)
                if match:
                    val = match.group(0)

            # Case A: PULSE Source (Vsw)
            if "PULSE" in val:
                # Pattern: PULSE(v1 v2 td tr tf ton per)
                clean_val = val.replace(',', ' ')
                m = re.search(r"PULSE\((.*?)\)", clean_val)
                if m:
                    args = m.group(1).split()
                    if len(args) >= 7:
                        # ton is index 5
                        ton = args[5]
                        
                        param_name = f"Ton_{comp}"
                        
                        # Create param
                        net.set_parameter(param_name, ton)
                        
                        # Update component model to use {Ton_comp}
                        args[5] = f"{{{param_name}}}"
                        new_args = " ".join(args)
                        new_model = f"PULSE({new_args})"
                        
                        net.set_element_model(comp, new_model)
                        new_tunable.append(param_name)
                        updates_made = True
                        continue

            # Case B: Already Parameterized (Cout with {C_nom}, etc)
            if "{" in val and "}" in val:
                vars_found = re.findall(r"\{(\w+)\}", val)
                # Heuristic: If 'C_nom' is present (common pattern), use it
                if 'C_nom' in vars_found:
                     new_tunable.append('C_nom')
                     continue
                if len(vars_found) == 1:
                    new_tunable.append(vars_found[0])
                    continue

            # Case C: Standard Value (Rload 6, Cin 300u)
            # If value is simple number/string without spaces (and not a model like 'NMOS')
            if re.match(r"^[0-9\-\+\.a-zA-Z]+$", val) and " " not in val:
                 param_name = f"val_{comp}"
                 net.set_parameter(param_name, val)
                 net.set_component_value(comp, f"{{{param_name}}}")
                 new_tunable.append(param_name)
                 updates_made = True
                 continue
            
            # Fallback: keep original
            new_tunable.append(comp)

        if updates_made:
            net.write_netlist(netlist_path)
            
        return new_tunable
    except Exception as e:
        print(f"Warning: Auto-parameterization failed: {e}")
        return tunable_components

# =================================================================================================
# STAGE 1: THE CONSULTANT (Requirement Gathering)
# =================================================================================================

class OptimizationSpecs(TypedDict):
    """The 'Contract' generated by the Consultant for the Engineer."""
    circuit_path: str
    target_netlist: str
    target_raw: str
    tunable_parameters: List[str] # List of components/params to change (e.g. ['R1', 'C1', '{param}duty'])
    optimization_goals: str # Human readable goals (e.g. "Vout=5V, Ripple<10mV")
    metric_extraction_hint: str # Instructions for the code generator (e.g. "Trace is V(n001)")

class ConsultantState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    circuit_path: str
    available_components: List[str]
    available_params: List[str]
    is_ready: bool
    specs: Optional[OptimizationSpecs]

# --- Consultant Tools ---

@tool
def analyze_circuit_structure(file_path: str) -> str:
    """
    Parses a LTSpice Circuit (.asc or .net) to find all Components and Parameters.
    Returns lists of what can be modified.
    """
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    
    try:
        # We use SpiceEditor to find components, but we might need manual parsing for params
        netlist = SpiceEditor(file_path)
        components = netlist.get_components()
        
        # Manual parse for .param and to gather more info
        params = []
        with open(file_path, 'r', encoding='latin-1') as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith('.param'):
                    # format: .param name=value or .param name value
                    parts = line.split()
                    if len(parts) > 1:
                        params.append(line)
        
        return (f"Analysis of {os.path.basename(file_path)}:\n"
                f"--- Parameters (.param) ---\n" + "\n".join(params) + "\n\n"
                f"--- Components ---\n" + ", ".join(components[:20]) + 
                (f"... (+{len(components)-20} more)" if len(components)>20 else "") + "\n"
                "\nUsage: specific components can be updated directly, or parameters can be tuned.")
    except Exception as e:
        return f"Error parsing circuit: {str(e)}"

# --- Consultant Node ---

def consultant_node(state: ConsultantState):
    """
    Chat with the user to refine specs.
    """
    # System prompt to guide the Consultant
    system_prompt = (
        "You are an Expert Power Electronics Consultant. "
        "Your goal is to prepare a robust 'Optimization Specification' for the engineering agent.\n"
        "1. Analyze the circuit provided by the user using `analyze_circuit_structure`.\n"
        "2. Discuss with the user to identify:\n"
        "   - Which parameters/components are 'tunable' (the knobs).\n"
        "   - What are the precise goals (Vout target, efficiency, etc.).\n"
        "   - Which nodes/traces correspond to these goals (e.g. 'Is V(n001) the output?').\n"
        "3. Once you have ALL info, output the FINAL CONFIRMATION in this format:\n"
        "   'READY_TO_OPTIMIZE: {JSON_representation_of_OptimizationSpecs}'\n"
        "   Make sure tunable_parameters are exact names found in the file.\n"
        "   IMPORTANT: If the user asks to tune a property of a complex component (e.g. 'Duty Cycle', 'Ton', or 'Period' of Vsw), "
        "   you MUST list the COMPONENT NAME (e.g. 'Vsw') in the `tunable_parameters` list, not the abstract property name. "
        "   The Engineer will handle the detailed parameterization."
    )
    
    messages = [SystemMessage(content=system_prompt)] + state['messages']
    
    model = ChatOpenAI(model="gpt-4o", temperature=0.1)
    model = model.bind_tools([analyze_circuit_structure])
    
    response = model.invoke(messages)
    return {"messages": [response]}

def tools_consultant_node(state: ConsultantState):
    tools = [analyze_circuit_structure]
    return ToolNode(tools).invoke(state)

# =================================================================================================
# STAGE 2: THE ENGINEER (Optimization Loop)
# =================================================================================================

class EngineerState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    specs: OptimizationSpecs
    iteration: int
    current_metrics: Dict[str, float]
    best_metrics: Dict[str, float] # Track best so far? (Optional)

# --- Engineer Tools ---

@tool
def update_circuit_generic(changes: Dict[str, str], specs: OptimizationSpecs) -> str:
    """
    Updates the circuit parameters or components.
    Args:
        changes: Dict where key is param/component name, value is new value (str).
        specs: The OptimizationSpecs object (passed automatically in graph, but simplified here).
    """
    try:
        # In this simplified tool signature, we assume specs context is injected or file path is known.
        # We'll use the target netlist path from specs or hardcode standard behavior for this script.
        # Ideally, we pass simple args to LLM, so we hide 'specs' from the LLM perception if possible,
        # or just pass the file path.
        pass # Implementation is wrapped below to handle context
    except:
        pass

# We define the actual function that will be bound, closing over the file paths 
def create_engineer_tools(work_dir: str, netlist_name: str, raw_name: str):
    
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
            netlist = SpiceEditor(netlist_path)
            log = []
            
            for name, val in changes.items():
                # 1. Try to set as simple parameter (most robust for .param)
                try:
                    # SpiceEditor.set_parameter updates .param lines
                    netlist.set_parameter(name, val)
                    log.append(f"Set .param {name} = {val}")
                except:
                    # 2. If it's a component (R, L, C, V, M)
                    try:
                        # Identify if it is a model or value
                        # Heuristic: Value is usually number+suffix. Model is string.
                        # But set_component_value handles mostly R/L/C/V values.
                        netlist.set_component_value(name, val)
                        log.append(f"Set component {name} value = {val}")
                    except:
                        try:
                             # 3. Try set_element_model (for D, M, V_pulse)
                             netlist.set_element_model(name, val)
                             log.append(f"Set model {name} = {val}")
                        except Exception as e:
                             log.append(f"Failed to update {name}: {e}")
            
            netlist.write_netlist(netlist_path)
            return "Updates applied:\n" + "\n".join(log)
        except Exception as e:
            return f"Error updating circuit: {e}"

    @tool
    def simulate_circuit() -> str:
        """
        Runs the LTSpice simulation. Returns the output RAW filename if successful.
        """
        try:
            runner = SimRunner(output_folder=work_dir)
            netlist = SpiceEditor(netlist_path)
            # Ensure calling it by the netlist filename so RAW matches
            runner.run(netlist, run_filename=netlist_name)
            runner.wait_completion()
            
            if os.path.exists(raw_path):
                # Verify file size/validity briefly
                if os.path.getsize(raw_path) > 0:
                    return f"Simulation success. Output: {os.path.basename(raw_path)}"
                else:
                    return "Simulation failed: Empty RAW file."
            else:
                return "Simulation failed: No RAW file created (check LTSpice logs)."
        except Exception as e:
            return f"Error interacting with LTSpice: {e}"

    @tool
    def evaluate_results(python_script: str) -> str:
        """
        Executes a Python script to extract metrics from the simulation 'raw' file.
        
        The script MUST:
        1. Import necessary libraries (PyLTSpice.RawRead is available, numpy as np).
        2. Define a function `analyze(raw_file_path)` that returns a dictionary of results.
        3. Call this function at the end or simply run. 
        
        BUT to make it reliable, the Agent should just provide the LINES of code to run.
        The tool will wrap it.
        
        Pre-defined variables available in scope:
        - 'raw_path': string path to the raw file.
        - 'RawRead': class from PyLTSpice
        - 'np': numpy
        
        The script should print the result dictionary or assign it to a variable 'metrics'.
        """
        
        try:
            # Context for execution
            local_scope = {
                'raw_path': raw_path,
                'RawRead': RawRead,
                'np': np,
                'metrics': {}
            }
            
            # Additional safety: limit imports? (Mocking sandboxing for now)
            # We strictly execute the provided string
            exec(python_script, globals(), local_scope)
            
            # We expect the script to populate 'metrics' dict or print it.
            # If the LLM assigned to 'metrics', we use that.
            metrics = local_scope.get('metrics', {})
            
            if metrics:
                return f"Computed Metrics: {metrics}"
            else:
                return "Script ran but 'metrics' dictionary was empty. Ensure your script assigns result to variable 'metrics'."
                
        except Exception as e:
            err_msg = traceback.format_exc()
            return f"Error executing analysis script:\n{err_msg}"

    return [update_circuit, simulate_circuit, evaluate_results]

# --- Engineer Node ---

def engineer_node(state: EngineerState):
    """
    The Engineer agent loop.
    """
    iteration = state['iteration']
    specs = state['specs']
    
    # Prefix context
    # Create a metrics hint from the specs if not directly present
    metrics_hint = specs.get('metric_extraction_hint', 
                             f"Metrics to target: {specs.get('metrics', 'Check goal')}. "
                             f"Output Node: {specs.get('output_node', 'unknown')}")

    context = (
        f"Specs: {specs.get('optimization_goals', 'Meet requirements')}\n"
        f"Tunable Params: {specs.get('tunable_parameters', [])}\n"
        f"Metrics Hint: {metrics_hint}\n"
        f"Iteration: {iteration}"
    )
    
    messages = [SystemMessage(content=context)] + state['messages']

    model = ChatOpenAI(model="gpt-4o", temperature=0.2)
    # We need to recreate tools here or pass them. 
    # Since tools depend on file paths managed in main(), we assume they are bound in the Graph definition
    # For this function, we assume the node receives the bound tools implicitly?
    # No, LangGraph nodes usually don't carry the tools unless defined globally or in state?
    # We will bind tools inside the graph builder, this function just needs to call invoke.
    # To do that, we need access to the tool functions. 
    # A cleaner pattern: The graph is built with the tool node.
    
    # We can retrieve tools from a global or passed config? 
    # For this script structure, we'll initialize tools in main and bind them here.
    # But tools are closures over 'work_dir'. We need to be careful.
    # Solution: We will attach the 'tools' to the state or use a configurable runnable. 
    # FOR SIMPLICITY: We will reconstruct the tool list here using path from specs.
    
    work_dir = os.path.dirname(specs['target_raw'])
    netlist_name = os.path.basename(specs['target_netlist'])
    raw_name = os.path.basename(specs['target_raw'])
    
    tools = create_engineer_tools(work_dir, netlist_name, raw_name)
    model = model.bind_tools(tools)
    
    response = model.invoke(messages)
    
    return {
        "messages": [response], 
        "iteration_count": iteration + 1
    }

def engineer_tools_node(state: EngineerState):
    # Recreate tools to execute
    specs = state['specs']
    work_dir = os.path.dirname(specs['target_raw'])
    netlist_name = os.path.basename(specs['target_netlist'])
    raw_name = os.path.basename(specs['target_raw'])
    
    tools = create_engineer_tools(work_dir, netlist_name, raw_name)
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
    
    # Build mini graph for interactions
    workflow = StateGraph(ConsultantState)
    workflow.add_node("consultant", consultant_node)
    workflow.add_node("tools", tools_consultant_node)
    
    # Edges
    def should_continue(state):
        last_msg = state['messages'][-1]
        if last_msg.tool_calls:
            return "tools"
        # Check if Consultant produced final spec
        content = last_msg.content
        if "READY_TO_OPTIMIZE:" in content:
            return END
        return END # Wait for human input (handled by loop below)

    workflow.add_conditional_edges("consultant", should_continue)
    workflow.add_edge("tools", "consultant")
    workflow.set_entry_point("consultant")
    
    app = workflow.compile()
    
    # Chat Loop
    while True:
        # Run graph until it stops (either tool call needed OR waiting for user)
        # In this simple loop, we invoke app with current state.
        result = app.invoke(state)
        state = result # Update state with history
        
        last_msg = state['messages'][-1]
        
        # If tool executed, loop again to let Agent read output
        # (The graph logic above actually handles Agent->Tool->Agent. 
        # So 'result' is Agent's final response after tools).
        
        print(f"\n[Consultant]: {last_msg.content}")
        
        if "READY_TO_OPTIMIZE:" in last_msg.content:
            # extract JSON
            try:
                import json
                json_str = last_msg.content.split("READY_TO_OPTIMIZE:", 1)[1].strip()
                # Clean up markdown code blocks if present
                if json_str.startswith("```"):
                     json_str = json_str.strip("`json ")
                
                specs = json.loads(json_str)
                specs['circuit_path'] = circuit_path # Ensure path is correct
                return specs
            except Exception as e:
                print(f"Error parsing specs: {e}. Please try again.")

        # Get User Input
        user_input = input("\n[You]: ")
        if user_input.lower() in ['exit', 'quit']:
            sys.exit(0)
            
        state['messages'].append(HumanMessage(content=user_input))

def run_engineer_phase(specs: OptimizationSpecs):
    print("\n--- PHASE 2: ENGINEER ---")
    print(f"Goal: {specs['optimization_goals']}")
    
    # Setup working env
    work_dir = RESULTS_DIR
    original_net = specs['circuit_path']
    target_net = os.path.join(work_dir, "optimized_design.net")
    target_raw = os.path.join(work_dir, "optimized_design.raw")
    
    # Copy/Init
    shutil.copy(original_net, os.path.join(work_dir, "source.asc"))
    # Initialize netlist using SpiceEditor to ensure valid format
    try:
        net = SpiceEditor(original_net)
        net.write_netlist(target_net)
        
        # --- SOLUTION 3: Pre-processing / Parameterization ---
        # Automatically convert hardcoded components to parameters so the Agent can tune them easily.
        print("  -> Applying circuit parameterization...")
        new_params = parameterize_netlist(target_net, specs['tunable_parameters'])
        specs['tunable_parameters'] = new_params
        print(f"  -> Tunable parameters updated: {new_params}")
        
    except Exception as e:
        print(f"Error initializing netlist: {e}")
        return

    # Update Specs paths
    specs['target_netlist'] = target_net
    specs['target_raw'] = target_raw
    
    # System Instruction for Engineer
    engineer_sys_prompt = (
        "You are an Autonomous Power Engineering Agent.\n"
        "You operate in a loop:\n"
        "1. Update Circuit: Adjust 'tunable_parameters' to move towards goal.\n"
        "2. Simulate: Run the simulation.\n"
        "3. Evaluate: Write PYTHON CODE to inspect the RAW file and extract metrics.\n"
        "   - Use `RawRead` from PyLTSpice.\n"
        "   - Calculate errors relative to goals.\n" 
        "4. Repeat until goals are met or max iterations reached.\n\n"
        "IMPORTANT on Python Scripting:\n"
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
        "messages": [
            SystemMessage(content=engineer_sys_prompt),
            HumanMessage(content="Start optimization.")
        ],
        "specs": specs,
        "iteration": 0,
        "current_metrics": {},
        "best_metrics": {}
    }
    
    # Build Engineer Graph
    workflow = StateGraph(EngineerState)
    workflow.add_node("engineer", engineer_node)
    workflow.add_node("tools", engineer_tools_node)
    
    def should_continue_engineer(state):
        last_msg = state['messages'][-1]
        if last_msg.tool_calls:
            return "tools"
        # Since engineer is autonomous, if it didn't call a tool, it might be done or confused.
        # We check content for "OPTIMIZATION COMPLETE" or similar, otherwise loop?
        # Ideally it always calls tools until done.
        # Let's force it to end if it says "DONE"
        if "optimization complete" in last_msg.content.lower():
            return END
        if state['iteration'] > 20:
             return END
        return END # Safety break if no tool called, usually implies question or error

    workflow.add_conditional_edges("engineer", should_continue_engineer, {"tools": "tools", END: END})
    workflow.add_edge("tools", "engineer")
    workflow.set_entry_point("engineer")
    
    app = workflow.compile()
    
    print("Engineer Agent is running...(this may take time)")
    for event in app.stream(initial_state, config={"recursion_limit": 50}):
        pass # Stream execution
        if 'engineer' in event:
            msg = event['engineer']['messages'][-1]
            print(f"\n[Engineer]: {msg.content}")

def main():
    print("=== PowerAgent V3.0 ===")
    
    # 1. Find Circuit
    # Look for .asc file in current or parent folders
    default_circuit = None
    search_dirs = [BASE_DIR, os.path.join(BASE_DIR, '..', 'Circuits')]
    
    for d in search_dirs:
        if os.path.exists(d):
            files = [f for f in os.listdir(d) if f.endswith('.asc')]
            if files:
                default_circuit = os.path.join(d, files[0])
                break
    
    circuit_path = input(f"Enter circuit path [{default_circuit or 'path/to/file.asc'}]: ").strip()
    if not circuit_path and default_circuit:
        circuit_path = default_circuit
        
    if not circuit_path or not os.path.exists(circuit_path):
        print("Invalid circuit path.")
        return

    # 2. Run Phase 1
    specs = run_consultant_phase(circuit_path)
    if not specs:
        print("Consultation aborted.")
        return
        
    # 3. Run Phase 2
    run_engineer_phase(specs)

if __name__ == "__main__":
    main()
