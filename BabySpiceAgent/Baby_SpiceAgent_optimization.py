import os
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
prompt = f"""Here is the LTSPice net list:
{netlist_content}

Here is the results as voltage on the capacitor C, Vn2:
{data_str}

Change the R and/or C values to obtain a time constant of approximately 2 milliseconds.

Return a JSON object with the new R and C values only, in the format:
{{
  "R1": "value_in_ohms_with_unit",
  "C1": "value_in_farads_with_unit"
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
except Exception as e:
    print(f"Error calling OpenAI: {e}")


# If it gives reasonable value here we add the loop: 
#change the netlist using the new R and C values