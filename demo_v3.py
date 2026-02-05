"""
Demo: PowerAgent V3 (Interactive Mode)
=====================================
This script demonstrates how to launch the new "Human-in-the-Loop" interactive agent.

Instructions:
1. Ensure your OPENAI_API_KEY environment variable is set.
2. Run this script.
3. Follow the on-screen prompts.
   - Consultant Phase: Chat with the agent to analyze the circuit.
   - Mode Selection: Choose 'Safe Mode' or 'Live Mode'.
   - Engineer Phase: Approve simulations and optimization steps.
"""

import os
from spiceagent import PowerAgentV3

# Ensure API Key is set (for demo purposes if not globally set)
if "OPENAI_API_KEY" not in os.environ:
    # prompt user or error out
    print("Please set OPENAI_API_KEY environment variable.")
    # os.environ["OPENAI_API_KEY"] = "sk-..." 

def main():
    # 1. Initialize the Interactive Agent
    # You can customize the output directory
    agent = PowerAgentV3(output_dir="demo_run_v3_output")
    
    print("🤖 PowerAgent V3 (Interactive) initialized.")
    
    # 2. Get a target circuit
    # Ideally, point to an absolute path of your .asc file
    # For this demo, we'll ask user or look for a default
    default_circuit = os.path.abspath(os.path.join("Circuits", "Buck_converter", "Buck_converter_real.asc"))
    
    print(f"\nDefault circuit path suggestion: {default_circuit}")
    circuit_path = input("Enter .asc file path (or press Enter for default): ").strip()
    
    if not circuit_path:
        circuit_path = default_circuit
        
    if not os.path.exists(circuit_path):
        print(f"❌ File not found: {circuit_path}")
        return

    # 3. Start the Optimization Session
    # This launches the interactive CLI mode (Consultant -> Engineer)
    try:
        agent.optimize(circuit_path)
    except KeyboardInterrupt:
        print("\nSession interrupted by user.")
    except Exception as e:
        print(f"\n❌ Error during execution: {e}")

if __name__ == "__main__":
    main()
