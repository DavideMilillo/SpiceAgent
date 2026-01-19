from PyLTSpice import SpiceEditor
import os

netlist_path = r"c:\Users\david\Desktop\PhD\progetti\SpiceAgent\PowerAgent\results_v3\optimized_design.net"

# Create a clean version of the netlist for testing (remove the added params)
clean_netlist_content = """* Test Netlist
Vin in 0 12
Cin in 0 300u
D1 0 sw MBR745
L1 sw out Flux={L_nom}*{I_sat}*tanh(x/{I_sat})
M1 in N001 sw sw IRF1404
Rload out 0 6
Vsw N001 sw PULSE(0 10 0 1n 1n 3.38u 10u)
Cout out 0 Q={C_min}*x + ({C_nom}-{C_min})*{V_coeff}*tanh(x/{V_coeff})
.model D D
.model NMOS NMOS
.model PMOS PMOS
.tran 0 10m 0 100n
.param C_nom=10u C_min=1u V_coeff=4.5 L_nom=10u I_sat=2.5
.end
"""

with open("test_debug.net", "w") as f:
    f.write(clean_netlist_content)

net = SpiceEditor("test_debug.net")
components = net.get_components()
print(f"Components found: {components}")

# Test set_parameter on a component name
net.set_parameter("Vsw", "5")
print("\nAfter set_parameter('Vsw', '5'):")
# Read file content
with open("test_debug.net", "r") as f:
    print(f.read())

# Test set_component_value on Vsw
net = SpiceEditor("test_debug.net") # Reload
net.set_component_value("Vsw", "PULSE(0 5 0 1n 1n 3.38u 10u)")
print("\nAfter set_component_value('Vsw', 'PULSE(...)')")
with open("test_debug.net", "r") as f:
    print(f.read())
