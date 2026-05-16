"""
EG4 MCP Server - Model Context Protocol server for EG4 solar inverter monitoring and control.

This package provides comprehensive solar system monitoring and analysis capabilities
for EG4 inverters through the Model Context Protocol (MCP).

Features:
- Real-time monitoring of solar production, consumption, and system status
- Performance analysis with efficiency metrics and grid independence calculations
- Battery health monitoring with SOH, SOC, and cycle count tracking
- Smart alerts for proactive issue detection and notifications
- Maintenance insights with automated recommendations and scheduling
- Historical data analysis for energy production and consumption trends
- System health scoring with component-level health assessment

Example usage:
    from eg4_mcp_server import EG4MCPServer
    
    # The server is typically run via MCP protocol
    # See README for configuration instructions
"""

__version__ = "0.1.5"
__author__ = "Matt Dreyer"
__email__ = "matt_dreyer@hotmail.com"  # Replace with your actual email
__license__ = "MIT"
__description__ = "Model Context Protocol server for EG4 solar inverter monitoring and control"
__url__ = "https://github.com/matt-dreyer/EG4_MCP_server"

# Expose `main` so the `eg4-mcp-server` console script declared in
# pyproject.toml ([project.scripts] eg4-mcp-server = "eg4_mcp_server:main")
# can resolve. Without this import the entry point fails with ImportError
# at invocation time.
from .server import main

# Package metadata
__all__ = [
    "FastMCP",
    "get_api_instance",
    "format_power_value",
    "format_energy_value",
    "generate_recommendations",
    "main",
]