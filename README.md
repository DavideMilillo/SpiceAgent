# SpiceAgent

AI-powered agent that optimizes LTSpice circuit designs autonomously.

## Overview
SpiceAgent uses an LLM to analyze netlists, propose parameter modifications, and run LTSpice simulations iteratively until the circuit meets the target specifications. The agent is capable of optimizing non-linear circuits and features a design tailored for effective human-machine interaction, allowing for collaborative circuit refinement.

**Workflow:** Analyze → Propose → Simulate → Evaluate → Iterate

## Installation

You can install SpiceAgent directly from the source:

```bash
git clone https://github.com/DavideMilillo/SpiceAgent.git
cd SpiceAgent
pip install .
```

Or, you can install directly the python package: https://pypi.org/project/ltspice-agent/0.1.0/
pip install ltspice-agent==0.1.0

## Quick Start (V2)

The recommended way to use SpiceAgent is via the `PowerAgent` class:

```python
from spiceagent import PowerAgent

# 1. Initialize the agent
agent = PowerAgent(api_key="sk-...")

# 2. Define your initial guess and targets
initial_values = {
    'Vin': '12', 
    'Cin': '300u', 
    'L1': '14u', 
    'Cout': '38u', 
    'Rload': '6', 
    'Vsw': 'PULSE(0 10 0 1n 1n 4.4u 10u)',
    'D1': 'MBR745', 
    'M1': 'IRF1404'
}

targets = {
    "v_mean": 5.0,  # Target output voltage (Volts)
    "ripple": 10   # Maximum ripple allowed (%)
}

# 3. Run Optimization
# If circuit_path is None, it uses the built-in Buck Converter example.
# You can increase max_iterations if the agent needs more steps.
result = agent.optimize(
    circuit_path=None, 
    initial_values=initial_values,
    target_specs=targets,
    max_iterations=50
)

print("Optimization Complete!")
print("Final Values:", result.get("circuit_values"))
```

## Project Structure

### 📦 SpiceAgent Package (New Standard)
*   `src/spiceagent/`: The source code of the package.
*   `src/spiceagent/power_agent.py`: The main `PowerAgent` class logic.

### 🔌 Legacy Scripts (V1)
*   `PowerAgent/`: Contains the original V1.5 script and LangGraph experiments.
*   `BabySpiceAgent/`: Simple RC circuit prototype.

## Changelog
See [CHANGELOG.md](CHANGELOG.md) for version history.

## License
MIT License

## Author
Davide Milillo
