"""
SpiceAgent - PowerAgent V1 (LangGraph Implementation)
An AI-agent for optimization of power electronics circuits using LangGraph.

This agent optimizes a buck converter to meet specific voltage and ripple requirements.
It iteratively analyzes, simulates, and updates the circuit parameters.

Author: Davide Milillo 
"""

import os
import operator
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
MAX_ITERATIONS = 23
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
        # Always start from the base ASC to ensure clean state or load previous netlist?
        # Better to load the base ASC and apply all changes.
        # But the agent might want incremental changes. 
        # For simplicity, we assume the agent provides the values it wants to set.
        # If we want state persistence, we should read the last netlist.
        # Let's use the base ASC and apply the new values.
        
        netlist = SpiceEditor(CIRCUIT_ASC_PATH)
        updates_log = "Updating components:\n"
        for name, value in component_values.items():
            if name in ['Vsw', 'D1', 'M1']:
                 netlist.set_element_model(name, value)
            else:
                 netlist.set_component_value(name, value)
            updates_log += f"- {name} -> {value}\n"
        
        # Add simulation command if not present (usually in ASC, but good to ensure)
        # netlist.add_instructions(".tran 0 10m 0 100n") 
        
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

# --- Graph Nodes ---

def agent_node(state: AgentState):
    """
    The brain of the agent. Decides which tool to call or if it's done.
    """
    messages = state['messages']
    iteration_count = state['iteration_count']
    
    # Check iteration limit
    if iteration_count >= MAX_ITERATIONS:
        return {
            "messages": [AIMessage(content="Maximum iterations reached. Stopping optimization.")],
            "iteration_count": iteration_count
        }

    # Call the LLM
    model = ChatOpenAI(model="gpt-4", temperature=0)
    tools = [analyze_circuit, update_circuit, simulate_circuit, calculate_metrics]
    model_with_tools = model.bind_tools(tools)
    
    response = model_with_tools.invoke(messages)
    
    # Log the agent's thought process
    log_memory(f"**Agent Thought (Iter {iteration_count}):**\n{response.content}")
    
    return {"messages": [response], "iteration_count": iteration_count + 1}

def tool_node(state: AgentState):
    """
    Executes the tools requested by the agent.
    """
    # We use the prebuilt ToolNode logic manually or just use the ToolNode class
    tools = [analyze_circuit, update_circuit, simulate_circuit, calculate_metrics]
    tool_executor = ToolNode(tools)
    
    return tool_executor.invoke(state)

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

# --- Main Execution ---

def main():
    reset_memory()
    log_memory("# Optimization Session Started")
    
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
    
    initial_state = {
        "messages": [
            SystemMessage(content="You are an expert power electronics design agent. Your goal is to optimize a circuit to meet specifications."),
            HumanMessage(content=specifications)
        ],
        "iteration_count": 0
    }
    
    app = build_graph()
    
    print("Starting PowerAgent V1 (LangGraph)...")
    print(f"Logs will be written to {MEMORY_FILE}")
    
    # Run the graph
    for event in app.stream(initial_state):
        for key, value in event.items():
            print(f"Finished step: {key}")

if __name__ == "__main__":
    main()
