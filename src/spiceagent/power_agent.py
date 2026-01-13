"""
SpiceAgent - PowerAgent V2.0
An AI-agent for optimization of power electronics circuits.
"""

import os
import shutil
import operator
import importlib.resources
import numpy as np
from typing import TypedDict, Annotated, List, Dict, Optional, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from PyLTSpice import SimRunner, SpiceEditor, RawRead

# --- Types ---
class CircuitSpec(TypedDict):
    v_mean: float
    ripple_max_percent: float

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    iteration_count: int
    circuit_values: Dict[str, str]

# --- Helper to create tools with context ---
def create_tools(work_dir: str, netlist_name: str, raw_file_name: str):
    
    @tool
    def analyze_circuit() -> str:
        """
        Analyzes the circuit netlist and returns the current component values.
        Useful to know the current state of the circuit before making changes.
        """
        try:
            netlist_path = os.path.join(work_dir, netlist_name)
            if not os.path.exists(netlist_path):
                 # Fallback to finding a .net file
                 candidates = [f for f in os.listdir(work_dir) if f.endswith(".net")]
                 if candidates:
                     netlist_path = os.path.join(work_dir, candidates[0])
                 else:
                     return "Error: No netlist found."

            netlist = SpiceEditor(netlist_path)
            components = netlist.get_components()
            info = f"Current Configuration ({os.path.basename(netlist_path)}):\n"
            
            for component in components:
                try:
                    val = netlist.get_component_value(component)
                    info += f"- {component}: {val}\n"
                except:
                    pass
            
            # Simple parameter check
            with open(netlist_path, 'r') as f:
                for line in f:
                     if line.strip().lower().startswith(".param"):
                         info += f"{line.strip()}\n"
                         
            return info
        except Exception as e:
            return f"Error analyzing circuit: {str(e)}"

    @tool
    def update_circuit(component_values: Dict[str, str]) -> str:
        """
        Updates the circuit components with new values.
        Args:
            component_values: Dictionary of component names (L1, Cout) and values (150u, 10).
        """
        try:
            netlist_path = os.path.join(work_dir, netlist_name)
            netlist = SpiceEditor(netlist_path)
            
            updates_log = "Updating:\n"
            for name, value in component_values.items():
                if name in ['Cout', 'L1', 'C_nom', 'L_nom']:
                    # Try param first for flexibility
                    try:
                        netlist.set_component_value(name, value)
                    except:
                        netlist.set_parameter(name, value)
                elif name in ['Vsw', 'D1', 'M1']:
                    netlist.set_element_model(name, value)
                else:
                    try:
                        netlist.set_component_value(name, value)
                    except:
                        netlist.set_parameter(name, value)
                updates_log += f"- {name} -> {value}\n"
            
            netlist.write_netlist(netlist_path)
            return f"Updated successfully.\n{updates_log}"
        except Exception as e:
            return f"Error updating: {str(e)}"

    @tool
    def simulate_circuit() -> str:
        """
        Runs the simulation using the current netlist.
        """
        try:
            runner = SimRunner(output_folder=work_dir)
            netlist_path = os.path.join(work_dir, netlist_name)
            netlist = SpiceEditor(netlist_path)
            # We strictly enforce the run filename to match what we expect
            runner.run(netlist, run_filename=netlist_name)
            runner.wait_completion()
            
            # Check existance of RAW
            raw_path = os.path.join(work_dir, raw_file_name)
            if os.path.exists(raw_path):
                 return f"Simulation finished. Output: {os.path.basename(raw_path)}"
            else:
                 return "Simulation finished but RAW file was not created. Check logs."
        except Exception as e:
            return f"Error simulating: {str(e)}"

    @tool
    def calculate_metrics() -> str:
        """
        Calculates performance metrics (V_mean, Ripple, etc.) from the simulation results.
        """
        try:
            raw_path = os.path.join(work_dir, raw_file_name)
            if not os.path.exists(raw_path):
                return "Error: Raw simulation file not found."

            LTR = RawRead(raw_path)
            trace_names = LTR.get_trace_names()
            target_trace = next((t for t in trace_names if 'out' in t.lower() and 'v' in t.lower()), None)
            
            if not target_trace:
                 return f"Error: V(out) not found. Available: {trace_names}"

            v_out_trace = LTR.get_trace(target_trace)
            time_trace = LTR.get_trace('time')
            steps = LTR.get_steps()
            
            if not steps:
                return "Error: No simulation steps found."

            step = steps[0]
            time = time_trace.get_wave(step)
            voltage = v_out_trace.get_wave(step)

            if len(time) == 0:
                return "Error: Empty simulation data."

            # Steady state (last 30%)
            start_index = int(len(time) * 0.7)
            v_steady = voltage[start_index:]
            
            v_mean = np.mean(v_steady)
            v_max = np.max(v_steady)
            v_min = np.min(v_steady)
            ripple_pp = v_max - v_min
            ripple_percent = (ripple_pp / v_mean) * 100 if v_mean != 0 else 0
            
            return (f"Metrics:\n"
                    f"V_mean: {v_mean:.3f} V\n"
                    f"Ripple: {ripple_pp:.3f} V ({ripple_percent:.2f}%)\n"
                    f"Max: {v_max:.3f} V, Min: {v_min:.3f} V")

        except Exception as e:
            return f"Error calculating metrics: {str(e)}"

    return [analyze_circuit, update_circuit, simulate_circuit, calculate_metrics]

# --- PowerAgent Class ---

class PowerAgent:
    def __init__(self, model_name: str = "gpt-4o", temperature: float = 0.2, api_key: str = None):
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        self.model_name = model_name
        self.temperature = temperature
        
    def optimize(
        self,
        circuit_path: Optional[str] = None,
        initial_values: Optional[Dict[str, str]] = None,
        target_specs: Optional[Dict[str, float]] = None,
        max_iterations: int = 20,
        output_dir: str = "spiceagent_results"
    ) -> Dict[str, Any]:
        
        # 1. Setup
        os.makedirs(output_dir, exist_ok=True)
        if circuit_path is None:
             # Try via package resources, fall back to file relative
             try:
                 with importlib.resources.path("spiceagent.resources", "Buck_converter_real.asc") as p:
                     src = str(p)
             except Exception:
                 src = os.path.join(os.path.dirname(__file__), "resources", "Buck_converter_real.asc")
             
             circuit_name = "Buck_converter_real.asc"
             circuit_path = os.path.join(output_dir, circuit_name)
             shutil.copy(src, circuit_path)
        else:
             circuit_name = os.path.basename(circuit_path)
             dest = os.path.join(output_dir, circuit_name)
             shutil.copy(circuit_path, dest)
             circuit_path = dest

        # 2. Defaults
        if initial_values is None:
            initial_values = {
                'Vin': '12', 'Cin': '300u', 'L1': '14u', 'Cout': '30u', 'Rload': '6',
                'Vsw': 'PULSE(0 10 0 1n 1n 5u 10u)', 'D1': 'MBR745', 'M1': 'IRF1404'
            }
        if target_specs is None:
            target_specs = {"v_mean": 5.0, "ripple": 1.0}

        # 3. Init Netlist
        netlist_name = circuit_name.replace('.asc', '.net')
        raw_name = circuit_name.replace('.asc', '.raw')
        
        netlist_path = os.path.join(output_dir, netlist_name)
        netlist = SpiceEditor(circuit_path)
        for k, v in initial_values.items():
            try:
                if k in ['Vsw', 'D1', 'M1']:
                    netlist.set_element_model(k, v)
                else:
                    netlist.set_component_value(k, v)
            except:
                pass # Try best effort
        netlist.add_instructions(".tran 0 10m 0 100n")
        netlist.write_netlist(netlist_path)

        # 4. Create Tools
        tools = create_tools(output_dir, netlist_name, raw_name)

        # 5. Build Graph
        workflow = StateGraph(AgentState)
        
        def agent_node(state: AgentState):
            model = ChatOpenAI(model=self.model_name, temperature=self.temperature)
            model = model.bind_tools(tools)
            return {"messages": [model.invoke(state['messages'])], "iteration_count": state['iteration_count'] + 1}

        def tool_node_func(state: AgentState):
            executor = ToolNode(tools)
            result = executor.invoke(state)
            # Update circuit values tracking
            new_vals = state.get("circuit_values", {}).copy()
            last_msg = state['messages'][-1]
            if hasattr(last_msg, 'tool_calls'):
                for tc in last_msg.tool_calls:
                    if tc['name'] == 'update_circuit':
                        new_vals.update(tc['args'].get('component_values', {}))
            return {"messages": result['messages'], "circuit_values": new_vals}

        def should_continue(state: AgentState):
            if not state['messages'][-1].tool_calls:
                return "end"
            return "continue"

        workflow.add_node("agent", agent_node)
        workflow.add_node("tools", tool_node_func)
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges("agent", should_continue, {"continue": "tools", "end": END})
        workflow.add_edge("tools", "agent")
        
        app = workflow.compile()

        # 6. Run
        print(f"DEBUG: Starting optimization with initial values: {initial_values}")
        print(f"DEBUG: Target Specs: {target_specs}")

        system_msg = (
            "You are an expert power electronics agent.\n"
            f"Goal: V_mean = {target_specs['v_mean']}V, "
            f"Ripple < {target_specs.get('ripple', 1.0)}%.\n"
            "Analyze -> Simulate -> Calculate Metrics -> Update -> Repeat.\n"
            f"Initial Circuit State: {initial_values}"
        )
        
        initial_state = {
            "messages": [SystemMessage(content=system_msg), HumanMessage(content="Start optimization.")],
            "iteration_count": 0,
            "circuit_values": initial_values
        }

        final_state = initial_state
        print(f"Starting V2 optimization in {output_dir}...")
        try:
            for event in app.stream(initial_state, config={"recursion_limit": max_iterations}):
                for k, v in event.items():
                    if "circuit_values" in v:
                        final_state = v 
                    # Optional: print step info
                    if "messages" in v:
                         msg = v["messages"][-1]
                         if hasattr(msg, "content") and msg.content:
                             print(f"Agent: {msg.content}")
                         if hasattr(msg, "tool_calls") and msg.tool_calls:
                             print(f"Tool Call: {msg.tool_calls[0]['name']}")
        except Exception as e:
             # Capture the recursion limit or other errors to return the partial state
             print(f"Optimization stopped (e.g. recursion limit): {e}")
             
        return final_state
