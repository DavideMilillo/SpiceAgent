from PyLTSpice import SpiceEditor
import os

filename = "test_debug_cout.net"
clean_netlist_content = """* Test Netlist
Vsw N001 sw PULSE(0 10 0 1n 1n 3.38u 10u)
Cout out 0 Q={C_min}*x + ({C_nom}-{C_min})*{V_coeff}*tanh(x/{V_coeff})
.param C_nom=10u C_min=1u V_coeff=4.5
.end
"""

with open(filename, "w") as f:
    f.write(clean_netlist_content)

net = SpiceEditor(filename)
net.set_component_value("Cout", "47u")
net.write_netlist(filename)

with open(filename, "r") as f:
    print(f.read())
