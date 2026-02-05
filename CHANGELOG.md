# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-02-05
### Added
- **Interactive Mode**: Introduced `PowerAgentV3`, a Human-in-the-Loop agent that allows chat-based interaction during optimization.
- **Consultant Phase**: A diagnostic stage where the agent analyzes the circuit and helps define optimization goals before running simulations.
- **Live Mode (Windows)**: Experimental feature allowing real-time updates to the visible `.asc` schematic in LTSpice while the agent optimizes.
- **Parametrization**: Automatic discovery and parameterization of component values using LLM.

## [0.1.1] - 2026-01-15
### Added
- **Tolerance Control**: Added `v_mean_tolerance` parameter to the `optimize` method. You can now specify the allowed voltage error margin (default is +/- 0.1V).
- **Iteration Control**: Documented usage of `max_iterations` to control the optimization loop limit.

## [0.1.0] - 2026-01-13
- Initial release of SpiceAgent.
- Basic PowerAgent functionality with Buck Converter optimization.
