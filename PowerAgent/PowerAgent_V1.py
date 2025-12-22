"""First version of
SpiceAgent - PowerAgent
An AI-agent for optimization of power electronics circuits

The first version focuses on
finding the best parameters for a buck converter to achieve
specifications given.
The circuit is considered as fixed and in steady state.

Author: Davide Milillo
"""
import os

import PyLTSpice
from PyLTSpice import SimRunner
from PyLTSpice import SpiceEditor
from PyLTSpice import RawRead
import numpy as np
import matplotlib.pyplot as plt
from openai import OpenAI
from langchain import LangGraph
from langchain import LangChain


# function and tools
def calculate_metrics(time, voltage, current):

    """
    Calculate the metrics of the circuit simulations
    Args:
        time: time step values
        Voltage: Vout values 
        Current: Il vlaues
    Return:
        dictionary with all the parameters of the circuit

    """
    # 1. I consider only the steady state regime
    # so I cut off all the initial points
    start_index = int(len(time) * 0.5) 
    
    t_steady = time[start_index:]
    v_steady = voltage[start_index:]
    
    # 2. Calculate V_mean (DC Component)
    v_mean = np.mean(v_steady)
    
    # 3. Calculate  Ripple (Peak-to-Peak in steady regime)
    v_max = np.max(v_steady)
    v_min = np.min(v_steady)
    ripple_pp = v_max - v_min
    
    # 4. Calcolo Ripple %
    ripple_percent = (ripple_pp / v_mean) * 100 if v_mean != 0 else 0
    
    return {
        "v_mean": v_mean,
        "ripple_pp": ripple_pp,
        "ripple_percent": ripple_percent,
        "is_stable": True # stable if there are no errors
    }

def analyze_circuit(netlist):
    """
    See the LTSpice s netlist, the connections, the components and their values
    """
    netlist = SpiceEditor(circuit_path)

    #create the dictionary to return
    with open("Buck_converter_async_sim.net", 'r') as f:
        netlist_content = f.read()  
    return netlist_content

def simluate_circuit(netlist, y1, y2, yn):
    """
    Docstring for simluate_circuit
    
    :param netlist: the circuit netlist
    :param y1: variable 1 to analyze (such as the V(out))
    :param y2: variable 2 (e.g. I(L))
    :param yn: variable n

    Return
    target_trace: the actual points of the requested traces
    trace_names: full list of trace names, to use for knowing other traces
    """
    #run the simulation
    runner = SimRunner(output_folder='./')
    runner.run(netlist, run_filename= circuit_sim)
    runner.wait_completion()

    LTR = RawRead("Buck_converter_async_sim.raw")
    trace_names = LTR.get_trace_names()
    for y_i in range(len(y)):
        target_trace = trace_names[y_i]
        target_trace = trace_names[y_i].get_wave
    steps= LTR.get_steps()
    return target_trace, trace_names

def update_circuit(netlist, new_values):
    """
    Docstring for update_circuit
    
    :param netlist: the netlist of the circuit
    :param new_values: JSON of the new components values to use. This list should be coplete also if some components is still the same

    Return: done or errors
    """
    Vin = new_values[Vin]
    #and so the others

    netlist.set_component_value('Vin', Vin)  # Input voltage
    netlist.set_component_value('Cin', Cin)  # Input capacitor
    netlist.set_component_value('L1', L1)   #Inductor
    netlist.set_component_value('Cout', Cout)  #Output capacitor 
    netlist.set_component_value('Rload', Rload)    #Resistive load
    netlist.set_component_value('Vsw', Pulse_Vsw) #Switch control voltage

    done = False

    return (done=True)




# global variables
specifications = "V_mean = 5+-0.1 V, Vout_ripple < 50 mVpp %" 
circuit_path = circuit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Circuits', 'Buck_converter', 'Buck_converter_async.asc')
circuit_sim = "Buck_converter_async_sim.net"



# main
def main(void):
    """SpiceAgent-PowerAgent"""

    #circuit .asc

    #define the intial state

    #vergin prompt

    #full prompt


    #start PowerAgent
    #the flow of the agent is
    #analyze the specifications, type of circuit and actual values
    # PowerAgent decide what tools to call and how
    # 1) analyze_circuit for analyzieng better the netlist
    # 2) simulate_circuit to simulate the circuit in LTSpice
    # 3) update_circuit to change the values of the circuit's parameters
    # 4) calculate_metrics to caluclate the important characteristics of the simulation
    # 5) chatterA: call it if you are succesfull, it makes a summary and report the optimized circuit parameters
    # 6) chatterB: call it if you failed or you cannot fully satisfyed the specifics, it explain what it did and suggests new specifications
    # 7) STATE and/or a file agent_memory.md in which we write everything the agent think (in summary), every step. 
    #       useful for us to debugging and for the agent to have conetxt of its previous steps.
    #       e.g. step1) PowerAgent has called analyze_circuit; step2) PowerAgent has thought this circuit is not optimized for this reason. ...

    #the ideal flow should be 1, 2, 3, 4, 1, 2,..., 6. And for each step also 7. But the agent can decide also different orders
    