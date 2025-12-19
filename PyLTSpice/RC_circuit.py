#Test PyLTSpice RC Circuit Example
import PyLTSpice
from PyLTSpice import SimRunner
from PyLTSpice import SpiceEditor
from PyLTSpice import RawRead
import numpy as np


#open the circuit
#how can I convert the circuit file to a netlist object?
netlist = SpiceEditor('RC_circuit.net') 


#read the circuit file


#print the circuit file


#modify the values
netlist.set_component_value('R1', '10k') #R value
netlist.set_component_value('C1', '100n') #C value

netlist.remove_instructions(".tran")   #remove any previous instructions

netlist.add_instructions(".tran", "10m") #tran analysis for 10ms

#run a simulation
n_sim = 100 #number of simulations

#ciclo di simulazione
runner = SimRunner(output_filename="RC_circuit_sim.raw", netlist=netlist)
runner.run(netlist)


#show the results