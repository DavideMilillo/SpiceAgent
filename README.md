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


#new version of README
# SpiceAgent

AI-powered agent that optimizes LTSpice circuit designs automatically.

## What is SpiceAgent?

SpiceAgent is an LLM-based agent that:
- **Reads** circuit netlists (SPICE)
- **Proposes** parameter modifications using AI
- **Simulates** changes with LTSpice
- **Iterates** to optimize circuit performance

Instead of manually tweaking component values 20+ times, SpiceAgent does it autonomously.



Project Structure
PowerAgent/ - Main circuit optimization agent (Buck converter with non-linear models)

BabySpiceAgent/ - Simplified prototype (RC circuit optimization)

PyLTSpice/ - PyLTSpice integration examples

Circuits/ - Sample circuit files

How It Works:
Analyze netlist → Propose changes (LLM) → Simulate (LTSpice) → Evaluate → Iterate
The agent reasons about circuit behavior and iteratively refines parameters until it converges.


See CONTRIBUTING.md for detailed guidelines.

License
MIT License - see LICENSE file.


Author
Davide Milillo - @DavideMilillo

