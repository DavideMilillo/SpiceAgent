# Contributing to SpiceAgent

Thanks for your interest in contributing! Here's how to get started.

## Setup

1. **Fork and clone**
   ```bash
   git clone https://github.com/YOUR_USERNAME/SpiceAgent.git
   cd SpiceAgent
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set OpenAI API key**
   
   You need an OpenAI API key for the agent to function.
   ```bash
   # Option 1: Set in terminal
   export OPENAI_API_KEY="your_key_here"  # Windows (PowerShell): $env:OPENAI_API_KEY="your_key_here"
   
   # Option 2: Use a .env file (requires python-dotenv or manual loading)
   echo "OPENAI_API_KEY=your_key_here" > .env
   ```
   *Note: `.env` is in `.gitignore` and won't be committed.*

## Making Changes

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Code style**
   *   Follow **PEP 8**.
   *   Add **docstrings** to functions/classes.
   *   Keep commits focused and atomic.

3. **Test your changes**
   *   Run any relevant circuit optimization tests.
   *   Verify simulations complete without errors.
   *   Test both ideal and non-linear component models.

4. **Commit messages**
   *   Use clear, descriptive messages.
   *   Example: `Add non-linear capacitor model to PowerAgent`
   *   Reference issues if applicable: `Fix #5`

## Submitting a Pull Request

1. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open a PR on GitHub**
   *   Describe what your PR does.
   *   Reference related issues (e.g., "Closes #5").
   *   Include any relevant test results or circuit simulation outputs.

3. **Code review**
   *   Be responsive to feedback.
   *   Keep discussions respectful and technical.

## Project Guidelines

### Directory Structure
*   `PowerAgent/` - Main optimization agent (all features here first).
*   `BabySpiceAgent/` - Simplified prototypes and experiments.
*   `PyLTSpice/` - PyLTSpice integration utilities.
*   `Circuits/` - Sample `.asc` circuit files.

### What We're Looking For
*   Bug fixes and improvements to existing agents.
*   Better non-linear component models.
*   Improved circuit analysis and netlist parsing.
*   Documentation improvements.
*   New circuit topologies to optimize.
*   New IDEA!


## Dependencies

**Main packages:**
*   `PyLTSpice` - Circuit simulation interface.
*   `numpy` - Numerical computing.
*   `matplotlib` - Plotting (for visualization).
*   `openai` - LLM API.
*   `langchain` / `langgraph` - Agent framework.

See `requirements.txt` for exact versions.

## Questions?

*   Open an issue on GitHub.
*   Ask in pull request discussions.

Happy contributing! 🚀
