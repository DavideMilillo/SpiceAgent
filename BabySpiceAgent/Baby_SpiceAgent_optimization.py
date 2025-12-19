import os
import json
import numpy as np
import matplotlib.pyplot as plt
from PyLTSpice import SimRunner, SpiceEditor, RawRead
from openai import OpenAI

#Optimization Agent for Baby Spice Agent

# Setup OpenAI client
# Ensure OPENAI_API_KEY is set in your environment variables
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Define paths
current_dir = os.path.dirname(os.path.abspath(__file__))
# RC_circuit.asc is in ../Circuits/RC_circuit/ relative to this script
circuit_path = os.path.join(current_dir, '..', 'Circuits', 'RC_circuit', 'RC_circuit.asc')
# Simulation results in the respective circuit folder
output_folder = os.path.dirname(circuit_path)

# Ensure output folder exists
if not os.path.exists(output_folder):
    os.makedirs(output_folder)

# Initialize SpiceEditor
print(f"Loading circuit from: {circuit_path}")
netlist = SpiceEditor(circuit_path)

# Modify values (same as RC_circuit.py)
netlist.set_component_value('R1', '10k')
netlist.set_component_value('C1', '100n')
netlist.set_element_model('V1', 'PULSE(0 5 0 1u 1u 5m 10m)')
netlist.add_instructions(".tran 10m")

# Run simulation
print("Running simulation...")
runner = SimRunner(output_folder=output_folder)
runner.run(netlist, run_filename="RC_circuit_sim.net")
runner.wait_completion()

# Read results
raw_file_path = os.path.join(output_folder, "RC_circuit_sim.raw")
LTR = RawRead(raw_file_path)
v_c1 = LTR.get_trace('V(n002)')
time = LTR.get_trace('time')
steps = LTR.get_steps()

# We'll use the first step (or the only step)
step = steps[0]
time_data = time.get_wave(step)
voltage_data = v_c1.get_wave(step)

# Plotting (as requested "after the plot")
plt.figure()
plt.plot(time_data, voltage_data)
plt.title("Voltage across C1")
plt.xlabel("Time (s)")
plt.ylabel("Voltage (V)")
plt.grid()
# plt.show() # Uncomment to see the plot
print("Plot generated.")

# Prepare data for LLM
# Downsample to avoid token limits (e.g., 50 points)
num_points = 50
indices = np.linspace(0, len(time_data) - 1, num_points, dtype=int)
sampled_time = time_data[indices]
sampled_voltage = voltage_data[indices]

# Format data string
data_str = "Time(s), Voltage(V)\n"
for t, v in zip(sampled_time, sampled_voltage):
    data_str += f"{t:.6f}, {v:.6f}\n"

#later try to comment this, the LLM should be able to evaluate the results without seeing the netlist
# Get Netlist content
with open(os.path.join(output_folder, "RC_circuit_sim.net"), 'r') as f:
    netlist_content = f.read()

# Construct Prompt
prompt = f"""
We are analyzing an RC circuit simulated in LTSpice.
Here is the LTSpice net list:
{netlist_content}

Here is the results as voltage on the capacitor C, Vn2:
{data_str}

Carefully analyze the RC circuit behavior and try to esitmate the tau constant, tau_old, 
of the circuit considering the voltage response on the capacitor.
Change the R and/or C values to obtain a time constant of 2 milliseconds.

Return ONLY a JSON object with the old time constant, tau_old, and new R and C values only.
IMPORTANT: Use standard SPICE suffixes (Meg, k, m, u, n, p) for values. 
DO NOT use Greek letters (like μ) or units (like Ω, F, Ohms, Farads). 
Use 'u' for micro.

Example format:
{{
  "tau_old": "5ms",
  "R1": "12k",
  "C1": "300n"
}}
"""

print("\nSending to OpenAI...")
try:
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an expert electronics engineer."},
            {"role": "user", "content": prompt}
        ]
    )

    answer = response.choices[0].message.content
    print("\n--- Agent Analysis ---")
    print(answer)

    # Parse JSON
    # Sometimes LLMs wrap JSON in markdown code blocks, so we clean it
    json_str = answer.replace("```json", "").replace("```", "").strip()
    data = json.loads(json_str)
    
    new_R = data.get("R1")
    new_C = data.get("C1")
    
    print(f"Extracted values: R1={new_R}, C1={new_C}")

    # LOOP
    # change the netlist using the new R and C values
    if new_R and new_C:
        print("Applying new values and rerunning simulation...")
        netlist.set_component_value('R1', new_R)
        netlist.set_component_value('C1', new_C)
        
        # Rerun simulation
        runner.run(netlist, run_filename="RC_circuit_sim_optimized.net")
        runner.wait_completion()
        
        # Read new results
        raw_file_path_opt = os.path.join(output_folder, "RC_circuit_sim_optimized.raw")
        LTR_opt = RawRead(raw_file_path_opt)
        v_c1_opt = LTR_opt.get_trace('V(n002)')
        time_opt = LTR_opt.get_trace('time')
        step_opt = LTR_opt.get_steps()[0]
        
        # Plot comparison
        plt.figure()
        plt.plot(time_data, voltage_data, label="Original")
        plt.plot(time_opt.get_wave(step_opt), v_c1_opt.get_wave(step_opt), label="Optimized", linestyle='--')
        plt.title("Voltage across C1: Optimization")
        plt.xlabel("Time (s)")
        plt.ylabel("Voltage (V)")
        plt.legend()
        plt.grid()
        plt.show()
        print("Optimization plot generated.")

except Exception as e:
    print(f"Error: {e}")