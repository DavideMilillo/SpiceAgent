"""
using PyLTSpice to analyze the buck converter
set the component values, run the simulation, and plot the results
"""
import os

import PyLTSpice
from PyLTSpice import SimRunner
from PyLTSpice import SpiceEditor
from PyLTSpice import RawRead
import numpy as np
import matplotlib.pyplot as plt
from openai import OpenAI


#open the buck converter circuit
circuit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Circuits', 'Buck_converter', 'Buck_converter_async.asc')
netlist = SpiceEditor(circuit_path)

#read the circuit
print("Buck converter Components: ", netlist.get_components())
for components in netlist.get_components():
    print(f"Component: {components}, Value: {netlist.get_component_value(components)}")



#set the buck converter's component values
netlist.set_component_value('Vin', '12')  # Input voltage
netlist.set_component_value('Cin', '200u')  # Input capacitor
netlist.set_component_value('L1', '500u')   #Inductor
netlist.set_component_value('Cout', '300u')  #Output capacitor 
netlist.set_component_value('Rload', '6k')    #Resistive load
netlist.set_element_model('Vsw', 'PULSE(0 10 0 1n 1n 5u 10u)')  #Switch control voltage
netlist.set_element_model('D1', 'MBR745') #diode
netlist.set_element_model('M1', 'IRF1404') #Mosfet-switch

#nameof the nodes: in, sw and out


#add instructions
netlist.add_instructions(".tran 0 10m 0 100n")

#run the simulation
runner = SimRunner(output_folder='./')
runner.run(netlist, run_filename="Buck_converter_async_sim.net")
runner.wait_completion()


#show the results
LTR = RawRead("Buck_converter_async_sim.raw")
print(LTR.get_trace_names())

v_out = LTR.get_trace('V(out)')  # Output voltage node
x = LTR.get_trace('time')  # Gets the time axis
steps = LTR.get_steps()

for step in steps:
    plt.plot(x.get_wave(step), v_out.get_wave(step), label=f"Step {step}")
plt.title("Output Voltage of Buck Converter")
plt.xlabel("Time (s)")  
plt.ylabel("Voltage (V)")
plt.legend()
plt.show()

#now let's analyze the results with LLM
# Setup OpenAI client

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Prepare data for LLM
# Downsample to avoid token limits (e.g., 100 points)
downsample_factor = max(1, len(x.get_wave(steps[0])) // 100)
sampled_time = x.get_wave(steps[0])[::downsample_factor]
sampled_v_out = v_out.get_wave(steps[0])[::downsample_factor]
data_points = [{"time": t, "v_out": v} for t, v in zip(sampled_time, sampled_v_out)]

# Create prompt for LLM
prompt = f"""You are an expert in power electronics.
Analyze the following output voltage data from a buck converter simulation. 
The data points are given as time (in seconds) and output voltage (in volts).
Data Points: {data_points}
Provide insights on the voltage behavior, stability, and any notable characteristics.
"""

# Get analysis from LLM
# response = client.chat.completions.create(
#     model="gpt-4",  
#     messages=[
#         {"role": "system", "content": "You are an electronical engineere."},
#         {"role": "user", "content": prompt}
#     ]
# )   
# print("LLM Analysis:", response.choices[0].message.content)