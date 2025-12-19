#Test PyLTSpice RC Circuit Example
import os
import PyLTSpice
from PyLTSpice import SimRunner
from PyLTSpice import SpiceEditor
from PyLTSpice import RawRead
import numpy as np
import matplotlib.pyplot as plt


#open the circuit
#how can I convert the circuit file to a netlist object?

circuit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'RC_circuit.asc')
netlist = SpiceEditor(circuit_path) 



#read and print the circuit file
print("Circuit Components:", netlist.get_components())
for component in netlist.get_components():
    print(f"Component: {component}, Value: {netlist.get_component_value(component)}")


#modify the values
netlist.set_component_value('R1', '10k') #R value
netlist.set_component_value('C1', '100n') #C value
#netlist.set_component_value('V1', '5')    #Voltage source value
netlist.set_element_model('V1', 'PULSE(0 5 0 1u 1u 5m 10m)') #set V1 to a pulse source

#netlist.remove_instruction(".tran")   #remove any previous instructions

netlist.add_instructions(".tran 10m") #tran analysis for 10ms

#run a simulation
runner = SimRunner(output_folder='./')
runner.run(netlist, run_filename="RC_circuit_sim.net")
runner.wait_completion()


#show the results
LTR = RawRead("RC_circuit_sim.raw")
print(LTR.get_trace_names())

v_c1 = LTR.get_trace('V(n002)')
x = LTR.get_trace('time')  # Gets the time axis
steps = LTR.get_steps()

for step in steps:
    plt.plot(x.get_wave(step), v_c1.get_wave(step), label=f"Step {step}")

plt.title("Voltage across C1")
plt.xlabel("Time (s)")
plt.ylabel("Voltage (V)")
plt.legend()
plt.grid()
plt.show()