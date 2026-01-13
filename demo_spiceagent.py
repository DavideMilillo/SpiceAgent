import os
import sys

# Ensure we can import from src if not installed yet (for testing without pip install)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

try:
    from spiceagent import PowerAgent
    print("[OK] Successfully imported PowerAgent from spiceagent package.")
except ImportError as e:
    print(f"[ERROR] Failed to import PowerAgent: {e}")
    sys.exit(1)

def main():
    print("Initializing PowerAgent...")
    # NOTE: You need to set your API Key here or in environment variables
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] Warning: OPENAI_API_KEY not found in environment.")
        print("   If you want to run the full agent, please set it.")
        # We continue just to test the class instantiation
    
    agent = PowerAgent(api_key=api_key)
    
    # Define a test case
    initial_values = {
        'Vin': '12', 
        'Cin': '300u', 
        'L1': '20u', 
        'Cout': '40u', 
        'Rload': '6', 
        'Vsw': 'PULSE(0 10 0 1n 1n 4u 10u)',
        'D1': 'MBR745', 
        'M1': 'IRF1404'
    }

    # Use a dummy output dir for testing
    test_dir = "demo_run_output"
    
    print(f"Starting demo optimization in '{test_dir}'...")
    
    try:
        # We run with max_iterations=20 to verify the full flow
        result = agent.optimize(
            initial_values=initial_values,
            target_specs={"v_mean": 5.0, "ripple": 10},
            max_iterations=20, 
            output_dir=test_dir
        )
        print("[OK] optimize() method executed successfully.")
        print(f"Result keys: {result.keys()}")
    except Exception as e:
        print(f"[ERROR] Error during optimization: {e}")

if __name__ == "__main__":
    main()
