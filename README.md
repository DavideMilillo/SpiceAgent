### SpiceAgent
LTSpice AI-agent for circuit and electronic simulation. 
Use the power of AI in LTSpice

## first experiment: PyLTSpice done 19Dec
Try PyLTSpice to connect python code to LTSpice program

## second experiment: Baby_SpiceAgent: done 19Dec
Use a simple LLM to analyze the netlist 
Then use the LLM to optimize the circuit
I tryed on a simple RC circuit to change the tau constant


## third experiment: PowerAgent: Buck converter: done 24 Dec
The real AI-agent that optimize the circuit parameters of a Buck converter.
In PowerAgent_LangGraph.py the agent optimizes the buck converter.
In PowerAgent_V1.5.py the agent optimizes the real buck converter.
I have to use a non linear model for the inductor and capacitor 
since the ideal buck was too easy to optimize (with analytical formulas).
The agent shows good results and abilitty to reason, iterate and
so make an empirical optimization of the circuit.

