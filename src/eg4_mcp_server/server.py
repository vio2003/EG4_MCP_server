# server.py

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from eg4_python.client import EG4Inverter

# Load environment variables
load_dotenv()

# Use consistent environment variable names
USERNAME = os.getenv("EG4_USERNAME")
PASSWORD = os.getenv("EG4_PASSWORD")
BASE_URL = os.getenv("EG4_BASE_URL") 
DEBUG = os.getenv("EG4_DEBUG", "0") == "1"

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(name="EG4")

# Global API instance for connection reuse
_api_instance: Optional[EG4Inverter] = None
_last_login_time: Optional[datetime] = None
LOGIN_CACHE_DURATION = timedelta(minutes=30)

async def get_api_instance() -> EG4Inverter:
    """Get or create API instance with session management."""
    global _api_instance, _last_login_time
    
    if not USERNAME or not PASSWORD:
        raise ValueError("EG4_USERNAME and EG4_PASSWORD must be set in environment variables")
    
    current_time = datetime.now()
    
    # Check if we need to login or re-login
    if (_api_instance is None or 
        _last_login_time is None or 
        current_time - _last_login_time > LOGIN_CACHE_DURATION):
        
        if _api_instance:
            await _api_instance.close()
        
        logger.info("Creating new API instance and logging in")
        _api_instance = EG4Inverter(
            username=USERNAME, 
            password=PASSWORD, 
            base_url=BASE_URL
        )
        
        ignore_ssl = os.getenv("EG4_DISABLE_VERIFY_SSL", "0") == "1"
        await _api_instance.login(ignore_ssl=ignore_ssl)
        _last_login_time = current_time
        
        # Auto-select first inverter if available
        inverters = _api_instance.get_inverters()
        if inverters:
            _api_instance.set_selected_inverter(inverterIndex=0)
            logger.info(f"Selected inverter: {inverters[0].serialNum}")
        else:
            logger.warning("No inverters found")
    
    return _api_instance

def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float, handling None, strings, and other types."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int, handling None, strings, and other types."""
    if value is None:
        return default
    try:
        return int(float(value))  # Convert to float first to handle string decimals
    except (ValueError, TypeError):
        return default

def format_power_value(value: Any, unit: str = "W") -> str:
    """Format power values with appropriate units."""
    if value is None:
        return "N/A"
    
    try:
        val = safe_float(value)
        if abs(val) >= 1000:
            return f"{val/1000:.2f} k{unit}"
        return f"{val:.1f} {unit}"
    except (ValueError, TypeError):
        return str(value)

def format_energy_value(value: Any, unit: str = "Wh") -> str:
    """Format energy values with appropriate units."""
    if value is None:
        return "N/A"
    
    try:
        val = safe_float(value)
        if abs(val) >= 1000000:
            return f"{val/1000000:.2f} M{unit}"
        elif abs(val) >= 1000:
            return f"{val/1000:.2f} k{unit}"
        return f"{val:.1f} {unit}"
    except (ValueError, TypeError):
        return str(value)

def generate_recommendations(runtime_data, energy_data, battery_data) -> List[str]:
    """Generate performance recommendations based on current data."""
    recommendations = []
    
    # Check solar generation
    current_solar = safe_float(getattr(runtime_data, 'ppvpCharge', 0))
    if current_solar < 100:  # Assuming daytime check could be improved
        recommendations.append("Solar generation is low - check for shading or panel cleanliness")
    
    # Check battery health
    if hasattr(battery_data, 'battery_units') and battery_data.battery_units:
        low_soh_units = [unit for unit in battery_data.battery_units 
                        if safe_float(getattr(unit, 'soh', 100)) < 90]
        if low_soh_units:
            recommendations.append(f"Battery units with low health detected: {len(low_soh_units)} units below 90% SOH")
    
    # Check grid dependency
    grid_power = safe_float(getattr(runtime_data, 'pToGrid', 0))
    if grid_power < 0:  # Importing from grid
        recommendations.append("Currently importing from grid - consider load balancing")
    
    if not recommendations:
        recommendations.append("System is operating within normal parameters")
    
    return recommendations


def _find_peak_generation_time(data_points) -> str:
    """Find the time of peak solar generation."""
    if not data_points:
        return "N/A"
    
    peak_point = max(data_points, key=lambda p: safe_float(p.solar_pv))
    if peak_point.datetime:
        return peak_point.datetime.strftime("%H:%M")
    return peak_point.time.split()[1][:5] if peak_point.time else "N/A"

def _find_peak_consumption_time(data_points) -> str:
    """Find the time of peak consumption."""
    if not data_points:
        return "N/A"
    
    peak_point = max(data_points, key=lambda p: safe_float(p.consumption))
    if peak_point.datetime:
        return peak_point.datetime.strftime("%H:%M")
    return peak_point.time.split()[1][:5] if peak_point.time else "N/A"

def _generate_daily_insights(daily_data) -> List[str]:
    """Generate actionable insights from daily data analysis."""
    insights = []
    
    # Solar generation insights
    peak_solar = safe_float(daily_data.peak_solar_generation)
    if peak_solar > 0:
        peak_time = _find_peak_generation_time(daily_data.data_points)
        insights.append(f"Peak solar generation of {format_power_value(peak_solar)} occurred at {peak_time}")
    
    # Battery usage insights
    soc_range = safe_float(daily_data.max_soc) - safe_float(daily_data.min_soc)
    if soc_range > 50:
        insights.append(f"Battery experienced significant cycling ({soc_range:.1f}% range) - good utilization")
    elif soc_range < 20:
        insights.append(f"Battery had minimal cycling ({soc_range:.1f}% range) - consider adjusting charge/discharge settings")
    
    # Grid dependency insights
    total_grid_import = safe_float(daily_data.total_grid_import_kwh)
    total_solar_gen = safe_float(daily_data.total_solar_generation_kwh)
    if total_grid_import > total_solar_gen * 0.5:
        insights.append("High grid dependency detected - consider load shifting or battery optimization")
    
    # Energy balance insights
    total_consumption = safe_float(daily_data.total_consumption_kwh)
    surplus = total_solar_gen - total_consumption
    if surplus > 5:
        insights.append(f"Significant energy surplus ({surplus:.1f} kWh) - good day for solar generation")
    elif surplus < -5:
        insights.append(f"Energy deficit ({abs(surplus):.1f} kWh) - consumption exceeded generation")
    
    # Export insights
    total_grid_export = safe_float(daily_data.total_grid_export_kwh)
    if total_grid_export > total_solar_gen * 0.2:
        insights.append("High grid export - consider increasing self-consumption through load scheduling")
    
    if not insights:
        insights.append("System operating efficiently with balanced energy flows")
    
    return insights

@mcp.tool("Fetch_Configuration")
async def fetch_configuration() -> str:
    """
    Query the EG4 API for the runtime status of the inverter
    """
    try:
        api = await get_api_instance()
        
        # Get available inverters
        inverters = api.get_inverters()
        
        if not inverters:
            return json.dumps({"error": "No inverters found"}, indent=2)

        # Fetch all data in parallel for better performance
        runtime_task = api.get_inverter_runtime_async()
        energy_task = api.get_inverter_energy_async()
        battery_task = api.get_inverter_battery_async()
        config_task = api.read_settings_async()
        
        runtime_data, energy_data, battery_data, config_data = await asyncio.gather(
            runtime_task, energy_task, battery_task, config_task,
            return_exceptions=True
        )
        
        # Handle any exceptions
        result = {
            "timestamp": datetime.now().isoformat(),
            "inverters": [str(inv) for inv in inverters],
            "selected_inverter": 0,
            "runtime_data": runtime_data if not isinstance(runtime_data, Exception) else f"Error: {runtime_data}",
            "energy_data": energy_data if not isinstance(energy_data, Exception) else f"Error: {energy_data}",
            "battery_data": battery_data if not isinstance(battery_data, Exception) else f"Error: {battery_data}",
            "configuration": config_data if not isinstance(config_data, Exception) else f"Error: {config_data}"
        }
        
        return json.dumps(result, indent=2, default=str)
        
    except Exception as e:
        logger.error(f"Error in fetch_configuration: {e}")
        error_result = {
            "error": f"Error fetching EG4 data: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }
        return json.dumps(error_result, indent=2)

@mcp.tool("Get_System_Details")
async def get_system_details(system_id: Optional[int] = None) -> str:
    """
    Get detailed information about a specific system including layout, sources, and summary.
    If no system_id provided, uses the first available system.
    """
    try:
        api = await get_api_instance()
        inverters = api.get_inverters()
        
        if not inverters:
            return json.dumps({"error": "No systems found"}, indent=2)
        
        # Use specified system or default to first
        if system_id is not None and system_id < len(inverters):
            api.set_selected_inverter(inverterIndex=system_id)
            selected_inverter = inverters[system_id]
        else:
            selected_inverter = inverters[0]
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "system_details": {
                "system_id": system_id or 0,
                "serial_number": selected_inverter.serialNum,
                "plant_name": selected_inverter.plantName,
                "plant_id": selected_inverter.plantId,
                "battery_type": selected_inverter.batteryType,
                "firmware_version": selected_inverter.fwVersion,
                "hardware_version": getattr(selected_inverter, 'hardwareVersion', 'N/A'),
                "phase": selected_inverter.phase,
                "device_type": selected_inverter.deviceType,
                "machine_type": getattr(selected_inverter, 'machineType', 'N/A')
            },
            "available_systems": [
                {
                    "index": i,
                    "serial": inv.serialNum,
                    "name": inv.plantName
                } for i, inv in enumerate(inverters)
            ]
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_system_details: {e}")
        return json.dumps({"error": f"Error getting system details: {str(e)}"}, indent=2)

@mcp.tool("Get_Current_Production")
async def get_current_production(system_id: Optional[int] = None) -> str:
    """
    Get today's production data and real-time system summary.
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get current runtime and energy data
        runtime_data = await api.get_inverter_runtime_async()
        energy_data = await api.get_inverter_energy_async()
        
        # Format the production summary
        result = {
            "timestamp": datetime.now().isoformat(),
            "system_status": getattr(runtime_data, 'statusText', 'Unknown'),
            "current_production": {
                "solar_power": format_power_value(getattr(runtime_data, 'ppvpCharge', 0)),
                "battery_discharge": format_power_value(getattr(runtime_data, 'pDisCharge', 0)),
                "grid_export": format_power_value(getattr(runtime_data, 'pToGrid', 0)),
                "home_consumption": format_power_value(getattr(runtime_data, 'pToUser', 0)),
                "eps_power": format_power_value(getattr(runtime_data, 'peps', 0))
            },
            "today_totals": {
                "solar_generation": format_energy_value(getattr(energy_data, 'todayYielding', 0)),
                "battery_charged": format_energy_value(getattr(energy_data, 'todayCharging', 0)),
                "battery_discharged": format_energy_value(getattr(energy_data, 'todayDischarging', 0)),
                "grid_import": format_energy_value(getattr(energy_data, 'todayImport', 0)),
                "grid_export": format_energy_value(getattr(energy_data, 'todayExport', 0)),
                "home_usage": format_energy_value(getattr(energy_data, 'todayUsage', 0))
            },
            "solar_panels": {
                "pv1_voltage": f"{safe_float(getattr(runtime_data, 'vpv1', 0)):.1f} V",
                "pv2_voltage": f"{safe_float(getattr(runtime_data, 'vpv2', 0)):.1f} V",
                "pv3_voltage": f"{safe_float(getattr(runtime_data, 'vpv3', 0)):.1f} V",
                "pv4_voltage": f"{safe_float(getattr(runtime_data, 'vpv4', 0)):.1f} V"
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_current_production: {e}")
        return json.dumps({"error": f"Error getting current production: {str(e)}"}, indent=2)

@mcp.tool("Get_Performance_Analysis")
async def get_performance_analysis(days_back: int = 7, system_id: Optional[int] = None) -> str:
    """
    Get comprehensive performance analysis including efficiency metrics and panel performance.
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get current data for analysis
        runtime_data = await api.get_inverter_runtime_async()
        energy_data = await api.get_inverter_energy_async()
        battery_data = await api.get_inverter_battery_async()
        
        # Calculate basic efficiency metrics with safe conversions
        total_generation = safe_float(getattr(energy_data, 'totalYielding', 0))
        total_usage = safe_float(getattr(energy_data, 'totalUsage', 0))
        total_grid_import = safe_float(getattr(energy_data, 'totalImport', 0))
        
        grid_independence = 0
        if total_usage > 0:
            grid_independence = max(0, (total_usage - total_grid_import) / total_usage * 100)
        
        # Battery performance
        battery_capacity = safe_float(getattr(runtime_data, 'batCapacity', 0))
        battery_efficiency = 0
        if hasattr(battery_data, 'battery_units') and battery_data.battery_units:
            valid_soh_values = [safe_float(getattr(unit, 'soh', 0)) for unit in battery_data.battery_units]
            valid_soh_values = [soh for soh in valid_soh_values if soh > 0]  # Filter out zero/invalid values
            if valid_soh_values:
                battery_efficiency = sum(valid_soh_values) / len(valid_soh_values)
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "analysis_period": f"Last {days_back} days",
            "performance_metrics": {
                "grid_independence": f"{grid_independence:.1f}%",
                "total_generation": format_energy_value(total_generation),
                "total_consumption": format_energy_value(total_usage),
                "total_grid_import": format_energy_value(total_grid_import),
                "battery_efficiency": f"{battery_efficiency:.1f}%" if battery_efficiency > 0 else "N/A"
            },
            "system_health": {
                "inverter_status": getattr(runtime_data, 'statusText', 'Unknown'),
                "battery_count": safe_int(getattr(runtime_data, 'batParallelNum', 0)),
                "battery_capacity": f"{battery_capacity:.1f} Ah" if battery_capacity > 0 else "N/A"
            },
            "recommendations": generate_recommendations(runtime_data, energy_data, battery_data)
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_performance_analysis: {e}")
        return json.dumps({"error": f"Error performing analysis: {str(e)}"}, indent=2)

@mcp.tool("Get_Historical_Data")
async def get_historical_data(
    system_id: Optional[int] = None,
    days_back: int = 30,
    level: str = "day"
) -> str:
    """
    Get historical production data for analysis.

    Args:
        system_id: System ID (optional, uses first system if not provided)
        days_back: Number of days of historical data (default: 30)
        level: Data granularity - "minute", "hour", or "day" (default: "day")
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Try to get today's detailed data using daily chart
        today_detailed = None
        try:
            today_chart = await api.get_daily_chart_data_async()
            if today_chart.success:
                today_detailed = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "solar_generation": f"{safe_float(today_chart.total_solar_generation_kwh):.2f} kWh",
                    "consumption": f"{safe_float(today_chart.total_consumption_kwh):.2f} kWh",
                    "grid_import": f"{safe_float(today_chart.total_grid_import_kwh):.2f} kWh",
                    "grid_export": f"{safe_float(today_chart.total_grid_export_kwh):.2f} kWh",
                    "peak_solar": format_power_value(today_chart.peak_solar_generation),
                    "peak_consumption": format_power_value(today_chart.peak_consumption),
                    "data_points": today_chart.total_data_points,
                    "battery_soc_range": f"{safe_float(today_chart.min_soc):.1f}% - {safe_float(today_chart.max_soc):.1f}%"
                }
        except Exception as e:
            logger.warning(f"Could not get detailed daily chart data: {e}")
        
        # Get general energy data
        energy_data = await api.get_inverter_energy_async()
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "period": f"Last {days_back} days",
            "granularity": level,
            "today_detailed": today_detailed,
            "lifetime_totals": {
                "total_generation": format_energy_value(getattr(energy_data, 'totalYielding', 0)),
                "total_consumption": format_energy_value(getattr(energy_data, 'totalUsage', 0)),
                "total_battery_charged": format_energy_value(getattr(energy_data, 'totalCharging', 0)),
                "total_battery_discharged": format_energy_value(getattr(energy_data, 'totalDischarging', 0)),
                "total_grid_import": format_energy_value(getattr(energy_data, 'totalImport', 0)),
                "total_grid_export": format_energy_value(getattr(energy_data, 'totalExport', 0))
            },
            "today_summary": {
                "generation": format_energy_value(getattr(energy_data, 'todayYielding', 0)),
                "consumption": format_energy_value(getattr(energy_data, 'todayUsage', 0)),
                "battery_charged": format_energy_value(getattr(energy_data, 'todayCharging', 0)),
                "battery_discharged": format_energy_value(getattr(energy_data, 'todayDischarging', 0)),
                "grid_import": format_energy_value(getattr(energy_data, 'todayImport', 0)),
                "grid_export": format_energy_value(getattr(energy_data, 'todayExport', 0))
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_historical_data: {e}")
        return json.dumps({"error": f"Error getting historical data: {str(e)}"}, indent=2)

@mcp.tool("Get_System_Alerts")
async def get_system_alerts(days_back: int = 30, system_id: Optional[int] = None) -> str:
    """
    Get recent alerts and system health information.
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get current system data to check for issues
        runtime_data = await api.get_inverter_runtime_async()
        battery_data = await api.get_inverter_battery_async()
        
        alerts = []
        warnings = []
        
        # Check system status
        status = getattr(runtime_data, 'statusText', 'Unknown')
        if status.lower() not in ['normal', 'running', 'ok']:
            alerts.append({
                "type": "system_status",
                "severity": "warning",
                "message": f"System status: {status}",
                "timestamp": datetime.now().isoformat()
            })
        
        # Check battery health
        if hasattr(battery_data, 'battery_units') and battery_data.battery_units:
            for unit in battery_data.battery_units:
                soh = safe_float(getattr(unit, 'soh', 100))
                soc = safe_float(getattr(unit, 'soc', 0))
                bat_index = getattr(unit, 'batIndex', 'Unknown')
                
                if soh < 80:
                    alerts.append({
                        "type": "battery_health",
                        "severity": "error",
                        "message": f"Battery {bat_index} SOH critical: {soh:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                elif soh < 90:
                    warnings.append({
                        "type": "battery_health",
                        "severity": "warning",
                        "message": f"Battery {bat_index} SOH low: {soh:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
                
                if soc < 10:
                    warnings.append({
                        "type": "battery_charge",
                        "severity": "warning",
                        "message": f"Battery {bat_index} charge low: {soc:.1f}%",
                        "timestamp": datetime.now().isoformat()
                    })
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "period": f"Last {days_back} days",
            "system_health": "Good" if not alerts else "Issues Detected",
            "alerts": alerts,
            "warnings": warnings,
            "summary": {
                "total_alerts": len(alerts),
                "total_warnings": len(warnings),
                "system_status": status
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_system_alerts: {e}")
        return json.dumps({"error": f"Error getting system alerts: {str(e)}"}, indent=2)

@mcp.tool("Get_System_Health")
async def get_system_health(system_id: Optional[int] = None) -> str:
    """
    Get comprehensive system health status combining multiple data sources.
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get all system data
        runtime_data = await api.get_inverter_runtime_async()
        energy_data = await api.get_inverter_energy_async()
        battery_data = await api.get_inverter_battery_async()
        
        # Calculate health scores
        inverter_health = 100 if getattr(runtime_data, 'statusText', '').lower() in ['normal', 'running', 'ok'] else 50
        
        battery_health = 100
        if hasattr(battery_data, 'battery_units') and battery_data.battery_units:
            valid_soh_values = [safe_float(getattr(unit, 'soh', 100)) for unit in battery_data.battery_units]
            valid_soh_values = [soh for soh in valid_soh_values if soh > 0]  # Filter out invalid values
            if valid_soh_values:
                battery_health = sum(valid_soh_values) / len(valid_soh_values)
        
        # Solar panel health (basic check) - fix the comparison issue
        solar_health = 100
        vpv_values = [
            safe_float(getattr(runtime_data, 'vpv1', 0)),
            safe_float(getattr(runtime_data, 'vpv2', 0)),
            safe_float(getattr(runtime_data, 'vpv3', 0)),
            safe_float(getattr(runtime_data, 'vpv4', 0))
        ]
        active_panels = sum(1 for v in vpv_values if v > 10)  # Now safe since all values are floats
        
        overall_health = (inverter_health + battery_health + solar_health) / 3
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "overall_health": {
                "score": f"{overall_health:.1f}%",
                "status": "Excellent" if overall_health >= 90 else "Good" if overall_health >= 70 else "Fair" if overall_health >= 50 else "Poor"
            },
            "component_health": {
                "inverter": {
                    "score": f"{inverter_health:.1f}%",
                    "status": getattr(runtime_data, 'statusText', 'Unknown'),
                    "uptime": "Active" if inverter_health > 50 else "Issues"
                },
                "battery_system": {
                    "score": f"{battery_health:.1f}%",
                    "unit_count": len(getattr(battery_data, 'battery_units', [])),
                    "capacity": f"{safe_float(getattr(runtime_data, 'batCapacity', 0)):.1f} Ah"
                },
                "solar_panels": {
                    "score": f"{solar_health:.1f}%",
                    "active_strings": active_panels,
                    "voltages": {
                        "pv1": f"{vpv_values[0]:.1f} V",
                        "pv2": f"{vpv_values[1]:.1f} V",
                        "pv3": f"{vpv_values[2]:.1f} V",
                        "pv4": f"{vpv_values[3]:.1f} V"
                    }
                }
            },
            "current_performance": {
                "solar_generation": format_power_value(getattr(runtime_data, 'ppvpCharge', 0)),
                "battery_power": format_power_value(getattr(runtime_data, 'pDisCharge', 0)),
                "home_consumption": format_power_value(getattr(runtime_data, 'pToUser', 0)),
                "grid_interaction": format_power_value(getattr(runtime_data, 'pToGrid', 0))
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_system_health: {e}")
        return json.dumps({"error": f"Error getting system health: {str(e)}"}, indent=2)

@mcp.tool("Get_Maintenance_Insights")
async def get_maintenance_insights(
    system_id: Optional[int] = None,
    threshold_percent: float = 85.0
) -> str:
    """
    Get maintenance recommendations based on system performance analysis.
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get system data for analysis
        runtime_data = await api.get_inverter_runtime_async()
        battery_data = await api.get_inverter_battery_async()
        energy_data = await api.get_inverter_energy_async()
        
        recommendations = []
        maintenance_tasks = []
        
        # Battery maintenance checks
        if hasattr(battery_data, 'battery_units') and battery_data.battery_units:
            for unit in battery_data.battery_units:
                soh = safe_float(getattr(unit, 'soh', 100))
                cycles = safe_int(getattr(unit, 'cycleCnt', 0))
                bat_index = getattr(unit, 'batIndex', 'Unknown')
                
                if soh < threshold_percent:
                    maintenance_tasks.append({
                        "priority": "high",
                        "component": f"Battery {bat_index}",
                        "issue": f"SOH below threshold ({soh:.1f}% < {threshold_percent}%)",
                        "recommendation": "Consider battery replacement or professional inspection",
                        "estimated_effort": "2-4 hours (professional required)"
                    })
                
                if cycles > 5000:  # Typical cycle life threshold
                    recommendations.append({
                        "priority": "medium",
                        "component": f"Battery {bat_index}",
                        "issue": f"High cycle count ({cycles} cycles)",
                        "recommendation": "Monitor closely for capacity degradation",
                        "estimated_effort": "Ongoing monitoring"
                    })
        
        # Solar panel maintenance
        vpv_values = [
            safe_float(getattr(runtime_data, 'vpv1', 0)),
            safe_float(getattr(runtime_data, 'vpv2', 0)),
            safe_float(getattr(runtime_data, 'vpv3', 0)),
            safe_float(getattr(runtime_data, 'vpv4', 0))
        ]
        
        low_voltage_panels = [i+1 for i, v in enumerate(vpv_values) if 0 < v < 20]
        if low_voltage_panels:
            maintenance_tasks.append({
                "priority": "medium",
                "component": f"Solar Panel String(s) {low_voltage_panels}",
                "issue": "Low voltage detected",
                "recommendation": "Check for shading, dirt, or connection issues",
                "estimated_effort": "1-2 hours"
            })
        
        # General maintenance recommendations
        recommendations.extend([
            {
                "priority": "low",
                "component": "Solar Panels",
                "issue": "Routine maintenance",
                "recommendation": "Clean panels and check for physical damage",
                "estimated_effort": "1 hour",
                "frequency": "Monthly"
            },
            {
                "priority": "low", 
                "component": "Inverter",
                "issue": "Routine maintenance",
                "recommendation": "Check ventilation and clean air filters",
                "estimated_effort": "30 minutes",
                "frequency": "Quarterly"
            }
        ])
        
        # Calculate next maintenance date
        next_maintenance = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "system_performance_threshold": f"{threshold_percent}%",
            "next_recommended_maintenance": next_maintenance,
            "urgent_tasks": [task for task in maintenance_tasks if task["priority"] == "high"],
            "recommended_tasks": [task for task in maintenance_tasks if task["priority"] == "medium"],
            "routine_maintenance": [rec for rec in recommendations if rec["priority"] == "low"],
            "maintenance_summary": {
                "urgent_items": len([task for task in maintenance_tasks if task["priority"] == "high"]),
                "recommended_items": len([task for task in maintenance_tasks if task["priority"] == "medium"]),
                "routine_items": len([rec for rec in recommendations if rec["priority"] == "low"]),
                "overall_status": "Good" if not any(task["priority"] == "high" for task in maintenance_tasks) else "Attention Required"
            }
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_maintenance_insights: {e}")
        return json.dumps({"error": f"Error generating maintenance insights: {str(e)}"}, indent=2)


@mcp.tool("Get_Daily_Chart_Data")
async def get_daily_chart_data(
    system_id: Optional[int] = None,
    date_text: Optional[str] = None,
    analysis_type: str = "full"
) -> str:
    """
    Get detailed daily chart data with 10-minute interval time series analysis.
    
    Args:
        system_id: System ID (optional, uses first system if not provided)
        date_text: Date in YYYY-MM-DD format (optional, uses today if not provided)
        analysis_type: Type of analysis - "full", "summary", "hourly", "efficiency", or "raw"
    """
    try:
        api = await get_api_instance()
        
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)
        
        # Get daily chart data
        daily_data = await api.get_daily_chart_data_async(date_text)
        
        if not daily_data.success:
            return json.dumps({
                "error": "Failed to retrieve daily chart data",
                "timestamp": datetime.now().isoformat()
            }, indent=2)
        
        # Determine the date being analyzed
        analysis_date = date_text or datetime.now().strftime("%Y-%m-%d")
        
        # Base result structure
        result = {
            "timestamp": datetime.now().isoformat(),
            "analysis_date": analysis_date,
            "data_points": daily_data.total_data_points,
            "analysis_type": analysis_type
        }
        
        if analysis_type == "raw":
            # Return raw data points
            result["raw_data"] = [
                {
                    "time": point.time,
                    "solar_pv": safe_float(point.solar_pv),
                    "grid_power": safe_float(point.grid_power),
                    "battery_discharging": safe_float(point.battery_discharging),
                    "consumption": safe_float(point.consumption),
                    "soc": safe_float(point.soc),
                    "ac_couple_power": safe_float(point.ac_couple_power)
                } for point in daily_data.data_points
            ]
            
        elif analysis_type == "summary":
            # High-level summary
            result["daily_summary"] = {
                "solar_generation": f"{safe_float(daily_data.total_solar_generation_kwh):.2f} kWh",
                "total_consumption": f"{safe_float(daily_data.total_consumption_kwh):.2f} kWh",
                "grid_import": f"{safe_float(daily_data.total_grid_import_kwh):.2f} kWh",
                "grid_export": f"{safe_float(daily_data.total_grid_export_kwh):.2f} kWh",
                "peak_solar": format_power_value(daily_data.peak_solar_generation),
                "peak_consumption": format_power_value(daily_data.peak_consumption),
                "battery_soc_range": f"{safe_float(daily_data.min_soc):.1f}% - {safe_float(daily_data.max_soc):.1f}%",
                "average_soc": f"{safe_float(daily_data.average_soc):.1f}%"
            }
            
            # Calculate self-sufficiency and energy balance
            total_consumption_kwh = safe_float(daily_data.total_consumption_kwh)
            total_grid_import_kwh = safe_float(daily_data.total_grid_import_kwh)
            total_solar_generation_kwh = safe_float(daily_data.total_solar_generation_kwh)
            total_grid_export_kwh = safe_float(daily_data.total_grid_export_kwh)
            
            self_sufficiency = 0
            if total_consumption_kwh > 0:
                self_sufficiency = max(0, (total_consumption_kwh - total_grid_import_kwh) / total_consumption_kwh * 100)
            
            solar_utilization = 0
            if total_solar_generation_kwh > 0:
                solar_utilization = ((total_solar_generation_kwh - total_grid_export_kwh) / total_solar_generation_kwh * 100)
            
            result["energy_efficiency"] = {
                "self_sufficiency": f"{self_sufficiency:.1f}%",
                "solar_utilization": f"{solar_utilization:.1f}%",
                "energy_balance": f"{total_solar_generation_kwh - total_consumption_kwh:.2f} kWh"
            }
            
        elif analysis_type == "hourly":
            # Hourly breakdown analysis
            result["hourly_analysis"] = {
                "solar_generation": daily_data.get_solar_generation_by_hour(),
                "consumption": daily_data.get_consumption_by_hour()
            }
            
            # Find peak hours
            solar_hourly = daily_data.get_solar_generation_by_hour()
            consumption_hourly = daily_data.get_consumption_by_hour()
            
            peak_solar_hour = max(solar_hourly.items(), key=lambda x: x[1], default=(0, 0))
            peak_consumption_hour = max(consumption_hourly.items(), key=lambda x: x[1], default=(0, 0))
            
            result["peak_hours"] = {
                "peak_solar_hour": f"{peak_solar_hour[0]:02d}:00 ({peak_solar_hour[1]:.2f} kWh)",
                "peak_consumption_hour": f"{peak_consumption_hour[0]:02d}:00 ({peak_consumption_hour[1]:.2f} kWh)"
            }
            
        elif analysis_type == "efficiency":
            # Detailed efficiency analysis
            # Battery efficiency analysis
            charging_points = [p for p in daily_data.data_points if p.is_battery_charging]
            discharging_points = [p for p in daily_data.data_points if p.is_battery_discharging]
            
            total_charge_wh = sum(abs(safe_float(p.battery_discharging)) for p in charging_points) * (10/60)
            total_discharge_wh = sum(safe_float(p.battery_discharging) for p in discharging_points) * (10/60)
            
            battery_efficiency = 0
            if total_charge_wh > 0:
                battery_efficiency = (total_discharge_wh / total_charge_wh) * 100
            
            # Grid interaction analysis
            import_points = [p for p in daily_data.data_points if p.is_importing_from_grid]
            export_points = [p for p in daily_data.data_points if p.is_exporting_to_grid]
            
            total_grid_import_kwh = safe_float(daily_data.total_grid_import_kwh)
            total_grid_export_kwh = safe_float(daily_data.total_grid_export_kwh)
            total_solar_generation_kwh = safe_float(daily_data.total_solar_generation_kwh)
            total_consumption_kwh = safe_float(daily_data.total_consumption_kwh)
            
            result["efficiency_analysis"] = {
                "battery_round_trip_efficiency": f"{min(battery_efficiency, 100):.1f}%",
                "total_energy_charged": f"{total_charge_wh/1000:.2f} kWh",
                "total_energy_discharged": f"{total_discharge_wh/1000:.2f} kWh",
                "grid_interactions": {
                    "import_periods": len(import_points),
                    "export_periods": len(export_points),
                    "net_grid_usage": f"{total_grid_import_kwh - total_grid_export_kwh:.2f} kWh"
                },
                "energy_flows": {
                    "solar_to_consumption_direct": f"{min(total_solar_generation_kwh, total_consumption_kwh):.2f} kWh",
                    "battery_contribution": f"{total_discharge_wh/1000:.2f} kWh",
                    "grid_dependency": f"{total_grid_import_kwh:.2f} kWh"
                }
            }
            
        else:  # "full" analysis
            # Comprehensive analysis combining all above
            total_solar_generation_kwh = safe_float(daily_data.total_solar_generation_kwh)
            total_consumption_kwh = safe_float(daily_data.total_consumption_kwh)
            total_grid_import_kwh = safe_float(daily_data.total_grid_import_kwh)
            total_grid_export_kwh = safe_float(daily_data.total_grid_export_kwh)
            
            result["daily_summary"] = {
                "solar_generation": f"{total_solar_generation_kwh:.2f} kWh",
                "total_consumption": f"{total_consumption_kwh:.2f} kWh",
                "grid_import": f"{total_grid_import_kwh:.2f} kWh",
                "grid_export": f"{total_grid_export_kwh:.2f} kWh",
                "peak_solar": format_power_value(daily_data.peak_solar_generation),
                "peak_consumption": format_power_value(daily_data.peak_consumption),
                "battery_soc_range": f"{safe_float(daily_data.min_soc):.1f}% - {safe_float(daily_data.max_soc):.1f}%",
                "average_soc": f"{safe_float(daily_data.average_soc):.1f}%"
            }
            
            # Time-based analysis
            daytime_points = daily_data.filter_by_time_range(6, 18)  # 6 AM to 6 PM
            nighttime_points = daily_data.filter_by_time_range(18, 6)  # 6 PM to 6 AM (next day)
            
            daytime_consumption = sum(safe_float(p.consumption) for p in daytime_points) * (10/60) / 1000
            nighttime_consumption = sum(safe_float(p.consumption) for p in nighttime_points) * (10/60) / 1000
            
            result["time_analysis"] = {
                "daytime_consumption": f"{daytime_consumption:.2f} kWh",
                "nighttime_consumption": f"{nighttime_consumption:.2f} kWh",
                "daytime_solar": f"{sum(safe_float(p.solar_pv) for p in daytime_points) * (10/60) / 1000:.2f} kWh",
                "peak_generation_time": _find_peak_generation_time(daily_data.data_points),
                "peak_consumption_time": _find_peak_consumption_time(daily_data.data_points)
            }
            
            # Efficiency metrics
            self_sufficiency = 0
            if total_consumption_kwh > 0:
                self_sufficiency = max(0, (total_consumption_kwh - total_grid_import_kwh) / total_consumption_kwh * 100)
            
            solar_utilization = 0
            if total_solar_generation_kwh > 0:
                solar_utilization = ((total_solar_generation_kwh - total_grid_export_kwh) / total_solar_generation_kwh * 100)
            
            result["efficiency_metrics"] = {
                "self_sufficiency": f"{self_sufficiency:.1f}%",
                "solar_utilization": f"{solar_utilization:.1f}%",
                "energy_balance": f"{total_solar_generation_kwh - total_consumption_kwh:.2f} kWh"
            }
            
            # Generate insights and recommendations
            result["insights"] = _generate_daily_insights(daily_data)
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error in get_daily_chart_data: {e}")
        return json.dumps({
            "error": f"Error getting daily chart data: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }, indent=2)


# Map a human-friendly column name to the EG4 portal's energyType code.
# Anything not in this map is passed through verbatim (so callers can
# supply raw column names too).
ENERGY_TYPE_ALIASES = {
    "grid_to_load": "eToUserDay",
    "grid_import": "eToUserDay",
    "grid_to_battery": "eRecDay",
    "rectifier": "eRecDay",
    "grid_export": "eToGridDay",
    "battery_charge": "eChgDay",
    "battery_discharge": "eDisChgDay",
    "solar_pv1": "ePv1Day",
    "solar_pv2": "ePv2Day",
    "solar_pv3": "ePv3Day",
    "inverter_output": "eInvDay",
}


def _resolve_energy_type(name: str) -> str:
    return ENERGY_TYPE_ALIASES.get(name.lower(), name)


@mcp.tool("Get_Energy_Range")
async def get_energy_range(
    start_date: str,
    end_date: str,
    energy_type: str = "grid_import",
    system_id: Optional[int] = None,
) -> str:
    """Sum a single energy column across an arbitrary date range.

    Use this for questions like "how much grid power did I import between
    March 24 and April 24" or "what was my solar generation for last month."

    Args:
        start_date: Inclusive start date, ISO format (YYYY-MM-DD).
        end_date: Inclusive end date, ISO format (YYYY-MM-DD).
        energy_type: Either a friendly alias or a raw EG4 column name.
            Aliases (lower_snake_case):
              grid_import / grid_to_load   ->  eToUserDay   (typical "grid import")
              grid_to_battery / rectifier  ->  eRecDay
              grid_export                  ->  eToGridDay
              solar_pv1 / solar_pv2 / solar_pv3
              battery_charge / battery_discharge
              inverter_output              ->  eInvDay
            Raw EG4 column names (e.g. "eToUserDay") are passed through.
            Defaults to "grid_import".
        system_id: Optional inverter index (defaults to first inverter).

    Returns:
        JSON with the per-day breakdown, the per-month subtotals, and the
        range total in kWh. Pair eToUserDay + eRecDay if you want
        "total grid import including battery charging from grid."
    """
    try:
        from datetime import date as _date

        try:
            start = _date.fromisoformat(start_date)
            end = _date.fromisoformat(end_date)
        except ValueError as e:
            return json.dumps({"error": f"Invalid date: {e}"}, indent=2)
        if end < start:
            return json.dumps({"error": "end_date is before start_date"}, indent=2)

        column = _resolve_energy_type(energy_type)
        api = await get_api_instance()
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)

        # Iterate one month at a time. The API returns one full month
        # per call; we sum only the days that fall inside [start, end].
        per_day = []
        per_month = {}
        total_kwh = 0.0
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            data = await api.get_monthly_energy_async(year, month, energy_type=column)
            if not getattr(data, "success", False):
                return json.dumps({
                    "error": f"API call failed for {year}-{month:02d}: "
                             f"{getattr(data, 'error_message', 'unknown')}",
                    "energy_type_resolved": column,
                }, indent=2)
            month_start = max(start, _date(year, month, 1))
            # Last day of month: roll forward
            if month == 12:
                first_next = _date(year + 1, 1, 1)
            else:
                first_next = _date(year, month + 1, 1)
            month_end = min(end, _date(year, month, (first_next.toordinal() - _date(year, month, 1).toordinal())))
            month_sum = data.total_kwh_in_range(month_start.day, month_end.day)
            per_month[f"{year}-{month:02d}"] = round(month_sum, 2)
            total_kwh += month_sum
            for p in data.points:
                if month_start.day <= (p.day or 0) <= month_end.day:
                    per_day.append({
                        "date": f"{year}-{month:02d}-{p.day:02d}",
                        "kwh": round(p.energy_kwh, 2),
                    })
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1

        return json.dumps({
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "energy_type_requested": energy_type,
            "energy_type_resolved": column,
            "total_kwh": round(total_kwh, 2),
            "per_month_kwh": per_month,
            "per_day_kwh": per_day,
            "timestamp": datetime.now().isoformat(),
        }, indent=2)
    except Exception as e:
        logger.error(f"Error in get_energy_range: {e}")
        return json.dumps({
            "error": f"Error getting energy range: {str(e)}",
            "timestamp": datetime.now().isoformat(),
        }, indent=2)


@mcp.tool("Set_AC_Charge_SOC_Limit")
async def set_ac_charge_soc_limit(
    value: int,
    system_id: Optional[int] = None,
) -> str:
    """Set the inverter's "Stop AC Charge" SOC limit (HOLD_AC_CHARGE_SOC_LIMIT).

    When AC charging is enabled, the inverter pulls from the grid to charge the
    battery until it reaches this SOC percentage, then stops. Writes the value
    via the EG4 portal's remoteSet/write endpoint.

    Args:
        value: Target SOC percentage (integer, 10-100 inclusive).
        system_id: Optional inverter index (defaults to first inverter).

    Returns:
        JSON with success flag, the value written, and a timestamp.
    """
    try:
        if not isinstance(value, int) or value < 10 or value > 100:
            return json.dumps({
                "success": False,
                "error": f"value must be an integer between 10 and 100, got {value!r}",
                "timestamp": datetime.now().isoformat(),
            }, indent=2)

        api = await get_api_instance()
        if system_id is not None:
            api.set_selected_inverter(inverterIndex=system_id)

        success = await api.write_setting_async("HOLD_AC_CHARGE_SOC_LIMIT", str(value))
        logger.info(
            f"Set_AC_Charge_SOC_Limit value={value} success={success}"
        )
        return json.dumps({
            "success": bool(success),
            "hold_param": "HOLD_AC_CHARGE_SOC_LIMIT",
            "value": value,
            "timestamp": datetime.now().isoformat(),
        }, indent=2)
    except Exception as e:
        logger.error(f"Error in set_ac_charge_soc_limit: {e}")
        return json.dumps({
            "success": False,
            "error": f"Error setting AC charge SOC limit: {str(e)}",
            "timestamp": datetime.now().isoformat(),
        }, indent=2)


# Cleanup function for graceful shutdown
async def cleanup():
    """Clean up API connections on shutdown."""
    global _api_instance
    if _api_instance:
        try:
            await _api_instance.close()
        except Exception as e:
            logger.error(f"Error closing API instance: {e}")
        finally:
            _api_instance = None
            logger.info("API instance closed")

def main():
    """Main entry point for the EG4 MCP server."""
    import signal
    import sys
    
    def signal_handler(sig, frame):
        logger.info("Shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logger.info("Starting EG4 MCP Server...")
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        try:
            asyncio.run(cleanup())
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

# Run the MCP server
if __name__ == "__main__":
    main()