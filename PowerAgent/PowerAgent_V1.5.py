"""
SpiceAgent - PowerAgent V1.5 (real Buck Converter Optimization)
An AI-agent for optimization of power electronics circuits using LangGraph.

This agent optimizes a REAL buck converter to meet specific voltage and ripple requirements.
It iteratively analyzes, simulates, and updates the circuit parameters.

Author: Davide Milillo 
"""

import os
import operator
import shutil
import matplotlib.pyplot as plt
from typing import TypedDict, Annotated, List, Union, Dict, Any
import numpy as np
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from PyLTSpice import SimRunner, SpiceEditor, RawRead

# --- Configuration ---
MAX_ITERATIONS = 100
MEMORY_FILE = "agent_memory.md"
# Adjust paths to be absolute or relative to the script location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CIRCUIT_ASC_PATH = os.path.join(BASE_DIR, '..', 'Circuits', 'Buck_converter', 'Buck_converter_async.asc')
SIM_NETLIST_NAME = os.path.join(BASE_DIR, "Buck_converter_async_sim.net")
RAW_FILE_NAME = os.path.join(BASE_DIR, "Buck_converter_async_sim.raw")

# --- Helper Functions ---

def log_memory(message: str):
    """Logs a message to the agent_memory.md file."""
    with open(MEMORY_FILE, "a") as f:
        f.write(f"{message}\n\n")

def reset_memory():
    """Resets the agent_memory.md file."""
    with open(MEMORY_FILE, "w") as f:
        f.write("# PowerAgent Memory Log\n\n")

# --- Tools ---

@tool
def analyze_circuit(circuit_path: str = None) -> str:
    """
    Analyzes the circuit netlist and returns the current component values.
    Useful to know the current state of the circuit before making changes.
    """
    try:
        # Prefer the simulation netlist if it exists to show current state
        if circuit_path is None:
            if os.path.exists(SIM_NETLIST_NAME):
                circuit_path = SIM_NETLIST_NAME
                info_prefix = "Current Circuit Configuration (Simulation Netlist):"
            else:
                circuit_path = CIRCUIT_ASC_PATH
                info_prefix = "Current Circuit Configuration (Base ASC):"
        else:
            info_prefix = f"Current Circuit Configuration ({os.path.basename(circuit_path)}):"

        netlist = SpiceEditor(circuit_path)
        components = netlist.get_components()
        info = "Current Circuit Configuration (Base ASC):\n"
        for component in components:
            try:
                val = netlist.get_component_value(component)
                info += f"- {component}: {val}\n"
            except:
                pass
        
        log_memory(f"**Tool Call (analyze_circuit):**\n{info}")
        return info
    except Exception as e:
        return f"Error analyzing circuit: {str(e)}"

@tool
def update_circuit(component_values: Dict[str, str]) -> str:
    """
    Updates the circuit components with new values.
    Args:
        component_values: A dictionary where keys are component names (e.g., 'L1', 'Cout') 
                          and values are the new values as strings (e.g., '150u', '10').
    """
    try:
        # Load the current simulation netlist if it exists to preserve previous changes
        if os.path.exists(SIM_NETLIST_NAME):
            netlist = SpiceEditor(SIM_NETLIST_NAME)
        else:
            netlist = SpiceEditor(CIRCUIT_ASC_PATH)
            
        updates_log = "Updating components:\n"
        for name, value in component_values.items():
            if name in ['Vsw', 'D1', 'M1']:
                 netlist.set_element_model(name, value)
            else:
                 netlist.set_component_value(name, value)
            updates_log += f"- {name} -> {value}\n"
        
        netlist.write_netlist(SIM_NETLIST_NAME)
        
        log_memory(f"**Tool Call (update_circuit):**\n{updates_log}")
        return "Circuit updated successfully. Ready for simulation."
    except Exception as e:
        return f"Error updating circuit: {str(e)}"

@tool
def simulate_circuit() -> str:
    """
    Runs the simulation using the current netlist.
    Returns the path to the raw output file upon success.
    """
    try:
        log_memory("**Tool Call (simulate_circuit):** Starting simulation...")
        runner = SimRunner(output_folder=BASE_DIR)
        
        if not os.path.exists(SIM_NETLIST_NAME):
             return "Error: Netlist file not found. Run update_circuit first."

        # PyLTSpice runner needs the netlist object or file.
        netlist = SpiceEditor(SIM_NETLIST_NAME)
        runner.run(netlist, run_filename=SIM_NETLIST_NAME)
        runner.wait_completion()
        
        log_memory("**Tool Call (simulate_circuit):** Simulation completed.")
        return f"Simulation finished. Output saved to {RAW_FILE_NAME}"
    except Exception as e:
        return f"Error running simulation: {str(e)}"

@tool
def calculate_metrics() -> str:
    """
    Calculates performance metrics (V_mean, Ripple, etc.) from the simulation results.
    Should be called after simulate_circuit.
    """
    try:
        if not os.path.exists(RAW_FILE_NAME):
            return "Error: Raw simulation file not found. Run simulation first."

        LTR = RawRead(RAW_FILE_NAME)
        
        # Check available traces
        trace_names = LTR.get_trace_names()
        # We look for V(out) or similar
        target_trace = next((t for t in trace_names if 'out' in t.lower() and 'v' in t.lower()), None)
        
        if not target_trace:
             return f"Error: Output voltage trace not found. Available: {trace_names}"

        v_out_trace = LTR.get_trace(target_trace)
        time_trace = LTR.get_trace('time')
        steps = LTR.get_steps()
        
        step = steps[0]
        time = time_trace.get_wave(step)
        voltage = v_out_trace.get_wave(step)

        # 1. Steady state (last 30%)
        start_index = int(len(time) * 0.7)
        v_steady = voltage[start_index:]
        
        # 2. Metrics
        v_mean = np.mean(v_steady)
        v_max = np.max(v_steady)
        v_min = np.min(v_steady)
        ripple_pp = v_max - v_min
        ripple_percent = (ripple_pp / v_mean) * 100 if v_mean != 0 else 0
        
        metrics = {
            "v_mean": float(v_mean),
            "ripple_pp": float(ripple_pp),
            "ripple_percent": float(ripple_percent),
            "max_voltage": float(v_max),
            "min_voltage": float(v_min)
        }
        
        result_str = f"Simulation Metrics:\n{metrics}"
        log_memory(f"**Tool Call (calculate_metrics):**\n{result_str}")
        return result_str

    except Exception as e:
        return f"Error calculating metrics: {str(e)}"

# --- State Definition ---

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    iteration_count: int
    circuit_values: Dict[str, str]

# --- Graph Nodes ---

def agent_node(state: AgentState):
    """
    The brain of the agent. Decides which tool to call or if it's done.
    """
    messages = state['messages']
    iteration_count = state['iteration_count']
    circuit_values = state.get('circuit_values', {})
    
    # Check iteration limit
    if iteration_count >= MAX_ITERATIONS:
        return {
            "messages": [AIMessage(content="Maximum iterations reached. Stopping optimization.")],
            "iteration_count": iteration_count
        }

    # Inject current circuit state into the context if available
    if circuit_values:
        state_info = f"\nCurrent Known Circuit State: {circuit_values}\n"
        # We append this as a system message or just context to the last message?
        # Appending a SystemMessage is cleaner for the LLM to see the current state.
        # But we don't want to clutter the history with repeated states if we can avoid it.
        # Let's add it as a temporary system message for this turn.
        messages_with_context = [SystemMessage(content=state_info)] + messages
    else:
        messages_with_context = messages

    # Call the LLM
    model = ChatOpenAI(model="gpt-4", temperature=0.2)
    tools = [analyze_circuit, update_circuit, simulate_circuit, calculate_metrics]
    model_with_tools = model.bind_tools(tools)
    
    response = model_with_tools.invoke(messages_with_context)
    
    # Log the agent's thought process
    log_memory(f"**Agent Thought (Iter {iteration_count}):**\n{response.content}")
    
    return {"messages": [response], "iteration_count": iteration_count + 1}

def tool_node(state: AgentState):
    """
    Executes the tools requested by the agent and updates the circuit state.
    """
    tools = [analyze_circuit, update_circuit, simulate_circuit, calculate_metrics]
    tool_executor = ToolNode(tools)
    
    # Execute tools
    result = tool_executor.invoke(state)
    
    # Update circuit_values in state if update_circuit was called
    last_message = state['messages'][-1]
    new_values = state.get('circuit_values', {}).copy()
    
    if hasattr(last_message, 'tool_calls'):
        for tool_call in last_message.tool_calls:
            if tool_call['name'] == 'update_circuit':
                updates = tool_call['args'].get('component_values', {})
                new_values.update(updates)
                log_memory(f"**State Update:** Circuit values updated: {updates}")
    
    return {
        "messages": result['messages'],
        "circuit_values": new_values
    }

# --- Graph Construction ---

def should_continue(state: AgentState):
    """
    Determines the next node: tool execution or end.
    """
    messages = state['messages']
    last_message = messages[-1]
    
    if not last_message.tool_calls:
        return "end"
    
    return "continue"

def build_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    
    workflow.set_entry_point("agent")
    
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "continue": "tools",
            "end": END
        }
    )
    
    workflow.add_edge("tools", "agent")
    
    return workflow.compile()

def initial_state_circuit():
    """
    Initializes the circuit with default values and simulation commands.
    Saves the netlist to be used by the agent.
    Returns:
        Dict[str, str]: The initial component values.
    """
    initial_values = {
        'Vin': '12',
        'Cin': '300u',
        'L1': '10u',
        'Cout': '10u',
        'Rload': '6',
        'Vsw': 'PULSE(0 10 0 1n 1n 4.2u 10u)',
        'D1': 'MBR745',
        'M1': 'IRF1404'
    }
    try:
        netlist = SpiceEditor(CIRCUIT_ASC_PATH)
        
        # Set the buck converter's component values
        for name, value in initial_values.items():
            if name in ['Vsw', 'D1', 'M1']:
                netlist.set_element_model(name, value)
            else:
                netlist.set_component_value(name, value)

        # Add simulation instructions
        netlist.add_instructions(".tran 0 10m 0 100n")
        
        # Save the netlist
        netlist.write_netlist(SIM_NETLIST_NAME)
        log_memory("Initial circuit state set and netlist saved.")
        print("Initial circuit state initialized.")
        return initial_values
        
    except Exception as e:
        print(f"Error initializing circuit: {e}")
        log_memory(f"Error initializing circuit: {e}")
        return {}

def plot_comparison(initial_raw_path, final_raw_path):
    """
    Plots the initial and final voltage traces for comparison.
    """
    try:
        ltr_init = RawRead(initial_raw_path)
        ltr_final = RawRead(final_raw_path)
        
        def get_vout(ltr):
            trace_names = ltr.get_trace_names()
            target = next((t for t in trace_names if 'out' in t.lower() and 'v' in t.lower()), None)
            if target:
                return ltr.get_trace(target), ltr.get_trace('time')
            return None, None

        v_init, t_init = get_vout(ltr_init)
        v_final, t_final = get_vout(ltr_final)
        
        if v_init and v_final:
            # Prepare data
            steps_init = ltr_init.get_steps()
            time_init = t_init.get_wave(steps_init[0]) * 1000 # Convert to ms
            volt_init = v_init.get_wave(steps_init[0])
            
            steps_final = ltr_final.get_steps()
            time_final = t_final.get_wave(steps_final[0]) * 1000 # Convert to ms
            volt_final = v_final.get_wave(steps_final[0])

            # 1. Comparison Plot
            plt.figure(figsize=(10, 6))
            plt.plot(time_init, volt_init, label='Initial Design', linestyle='--', color='orange', alpha=0.7)
            plt.plot(time_final, volt_final, label='Optimized Design', linewidth=2, color='blue')
            plt.title('Buck Converter Optimization Results', fontsize=14)
            plt.xlabel('Time (ms)', fontsize=12)
            plt.ylabel('Output Voltage (V)', fontsize=12)
            plt.grid(True, which='both', linestyle='--', alpha=0.6)
            plt.legend(fontsize=10)
            plt.tight_layout()
            output_plot = os.path.join(BASE_DIR, "optimization_comparison.png")
            plt.savefig(output_plot, dpi=300)
            print(f"Comparison plot saved to {output_plot}")
            log_memory(f"Comparison plot saved to {output_plot}")
            plt.close()

            # 2. Initial Plot
            plt.figure(figsize=(10, 6))
            plt.plot(time_init, volt_init, label='Initial Design', color='orange')
            plt.title('Buck Converter: Initial Design', fontsize=14)
            plt.xlabel('Time (ms)', fontsize=12)
            plt.ylabel('Output Voltage (V)', fontsize=12)
            plt.grid(True, which='both', linestyle='--', alpha=0.6)
            plt.legend(fontsize=10)
            plt.tight_layout()
            output_init = os.path.join(BASE_DIR, "optimization_initial.png")
            plt.savefig(output_init, dpi=300)
            print(f"Initial plot saved to {output_init}")
            plt.close()

            # 3. Final Plot
            plt.figure(figsize=(10, 6))
            plt.plot(time_final, volt_final, label='Optimized Design', color='blue', linewidth=2)
            plt.title('Buck Converter: Optimized Design', fontsize=14)
            plt.xlabel('Time (ms)', fontsize=12)
            plt.ylabel('Output Voltage (V)', fontsize=12)
            plt.grid(True, which='both', linestyle='--', alpha=0.6)
            plt.legend(fontsize=10)
            plt.tight_layout()
            output_final = os.path.join(BASE_DIR, "optimization_final.png")
            plt.savefig(output_final, dpi=300)
            print(f"Final plot saved to {output_final}")
            plt.close()
        else:
            print("Error: Could not find V(out) traces for plotting.")
            
    except Exception as e:
        print(f"Error plotting comparison: {e}")

# --- Main Execution ---

def main():
    reset_memory()
    log_memory("# Optimization Session Started")
    
    # Initialize the circuit
    initial_values = initial_state_circuit()
    
    # Run baseline simulation
    print("Running baseline simulation...")
    runner = SimRunner(output_folder=BASE_DIR)
    netlist = SpiceEditor(SIM_NETLIST_NAME)
    runner.run(netlist, run_filename=SIM_NETLIST_NAME)
    runner.wait_completion()
    
    # Save initial raw file
    initial_raw_path = os.path.join(BASE_DIR, "initial_sim.raw")
    if os.path.exists(RAW_FILE_NAME):
        shutil.copy(RAW_FILE_NAME, initial_raw_path)
        print(f"Baseline simulation saved to {initial_raw_path}")
    
    # Define the goal
    specifications = (
        "Optimize the Buck Converter circuit to achieve:\n"
        "1. Mean Output Voltage (V_mean) = 5V ± 0.1V\n"
        "2. Output Ripple < 5% (approx < 0.25V)\n"
        "3. Stable operation.\n\n"
        "You have access to tools to analyze, update, simulate, and measure the circuit.\n"
        "Start by analyzing the current circuit, then simulate to get a baseline.\n"
        "Iterate by adjusting L1, Cout, or other parameters until specs are met.\n"
        "If you achieve the goal, summarize the final component values and metrics."
    )

    #define helping electronical instructions for the agent
    electronic_instructions = ("For the buck converter:\n"
    "1. To change the output voltage (Vmean), you MUST adjust the duty cycle (D).\n"
    "   - Formula: D = Vout / Vin. For 5V out with 12V in, D should be approx 0.417 (41.7%).\n"
    "   - Adjustment: D_new = D_old * (V_target / V_measured).\n"
    "2. To change the voltage Ripple, adjust Cout or L1.\n"
    "   - Increasing Cout decreases voltage ripple significantly.\n"
    "   - Increasing L1 decreases inductor current ripple, which also reduces voltage ripple.\n"
    "3. Constraints: Rload and Vin are fixed. Do NOT change them.\n"
    "4. Duty Cycle Implementation:\n"
    "   - The switch is controlled by Vsw = PULSE(V1 V2 Tdelay Trise Tfall Ton Tperiod).\n"
    "   - Duty Cycle D = Ton / Tperiod.\n"
    "   - Example: PULSE(0 10 0 1n 1n 4.2u 10u) -> Ton=4.2u, Tperiod=10u -> D=0.42.\n"
    "   - To set D=0.35, change Ton to 3.5u (keeping Tperiod=10u).\n")
    
    initial_state = {
        "messages": [
            SystemMessage(content="You are an expert power electronics design agent. Your goal is to optimize a circuit to meet specifications."),
            HumanMessage(content=specifications + "\n\n" + electronic_instructions)
        ],
        "iteration_count": 0,
        "circuit_values": initial_values
    }
    
    app = build_graph()
    
    print("Starting PowerAgent V1 (LangGraph)...")
    print(f"Logs will be written to {MEMORY_FILE}")
    
    # Run the graph
    for event in app.stream(initial_state, config={"recursion_limit": MAX_ITERATIONS}):
        for key, value in event.items():
            print(f"Finished step: {key}")

    # Run final simulation to ensure we have the latest data
    print("Running final simulation for comparison...")
    # Reload the netlist to ensure we simulate the latest version on disk
    netlist = SpiceEditor(SIM_NETLIST_NAME)
    runner.run(netlist, run_filename=SIM_NETLIST_NAME)
    runner.wait_completion()
    
    # Plot comparison
    plot_comparison(initial_raw_path, RAW_FILE_NAME)

if __name__ == "__main__":
    main()
