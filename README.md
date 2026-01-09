# SpiceAgent

AI-powered agent that optimizes LTSpice circuit designs autonomously.

## Overview
SpiceAgent uses an LLM to analyze netlists, propose parameter modifications, and run LTSpice simulations iteratively until the circuit meets the target specifications. The agent is capable of optimizing non-linear circuits and features a design tailored for effective human-machine interaction, allowing for collaborative circuit refinement.

**Workflow:** Analyze → Propose → Simulate → Evaluate → Iterate

## Project Structure

### 🔌 PowerAgent (Main)
The core agent using LangGraph to optimize non-linear Buck converters. It performs empirical optimization using a non-linear model for inductors and capacitors.
*   `PowerAgent/PowerAgent_LangGraph.py`: Main agent logic.
*   `PowerAgent/PowerAgent_V1.5.py`: Optimization setup for the real component model.

### 👶 Baby SpiceAgent
A simple prototype for analyzing and optimizing basic RC circuits.
*   `BabySpiceAgent/Baby_SpiceAgent.py`

### 🛠️ PyLTSpice Integration
Experiments connecting Python code to the LTSpice executable.
*   `PyLTSpice/`

## License
MIT License

## Author
Davide Milillo

